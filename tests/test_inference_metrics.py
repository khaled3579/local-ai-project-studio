import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from project_core import Workspace
from project_generation import request_body
from project_metrics import record_generation, stream_measurement
from project_stream import consume, GenerationInterrupted


class Response:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def iter_lines(self, **kwargs):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield b'data: ' + (event.encode() if isinstance(event, str) else json.dumps(event).encode())

    def close(self):
        self.closed = True


def events(finish='stop', done=True):
    values = [{'choices': [{'delta': {'content': 'ok'}, 'finish_reason': None}]},
              {'choices': [{'delta': {}, 'finish_reason': finish}]},
              {'choices': [], 'usage': {'prompt_tokens': 123, 'completion_tokens': 7,
                                       'prompt_tokens_details': {'cached_tokens': 40}},
               'timings': {'prompt_ms': 20, 'predicted_ms': 70, 'predicted_per_second': 100}}]
    return values + (['[DONE]'] if done else [])


class StreamMetrics(unittest.TestCase):
    def consume(self, response, **kwargs):
        self.rows = []
        return consume(response, lambda: False, lambda *args: None, measure=self.rows.append, **kwargs)

    def test_usage_only_frame_after_finish_is_measured(self):
        value = self.consume(Response(events()))
        self.assertEqual(value, {'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'})
        self.assertEqual(len(self.rows), 1)
        row = self.rows[0]
        self.assertEqual(row['prompt_tokens'], 123)
        self.assertEqual(row['completion_tokens'], 7)
        self.assertEqual(row['cached_tokens'], 40)
        self.assertEqual(row['predicted_per_second'], 100)
        self.assertEqual(row['outcome'], 'complete')
        self.assertGreaterEqual(row['first_token_seconds'], 0)

    def test_length_is_measured_as_incomplete(self):
        self.consume(Response(events('length')))
        self.assertEqual(self.rows[0]['outcome'], 'length')

    def test_disconnected_response_never_returns_partial_call(self):
        values = [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'x', 'function': {'name': 'write_file', 'arguments': '{"path":'}}]}}]}]
        with self.assertRaisesRegex(ValueError, 'انقطع'):
            self.consume(Response(values))
        self.assertEqual(self.rows[0]['outcome'], 'incomplete')
        self.assertIsNone(self.rows[0]['completion_tokens'])

    def test_interrupt_records_unknown_usage(self):
        rows = []
        with self.assertRaises(GenerationInterrupted):
            consume(Response(events()), lambda: True, lambda *args: None, measure=rows.append)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['prompt_tokens'])

    def test_tool_call_arguments_are_assembled_before_execution(self):
        frames = [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call', 'function': {'name': 'read_file', 'arguments': '{"path":'}}]}}]},
                  {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"a.py"}'}}]}, 'finish_reason': 'tool_calls'}]}, '[DONE]']
        value = self.consume(Response(frames))
        self.assertEqual(json.loads(value['message']['tool_calls'][0]['function']['arguments']), {'path': 'a.py'})

    def test_invalid_tool_json_rejected_and_receipt_incomplete(self):
        frames = [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c', 'function': {'name': 'write_file', 'arguments': '{'}}]}, 'finish_reason': 'tool_calls'}]}, '[DONE]']
        with self.assertRaises(ValueError):
            self.consume(Response(frames))
        self.assertEqual(self.rows[0]['outcome'], 'incomplete')

    def test_missing_usage_is_unknown(self):
        self.consume(Response([events()[0], events()[1], '[DONE]']))
        self.assertIsNone(self.rows[0]['prompt_tokens'])

    def test_observer_cannot_break_valid_generation(self):
        def fail(value):
            raise OSError('disk unavailable')
        value = consume(Response(events()), lambda: False, lambda *args: None, measure=fail)
        self.assertEqual(value['message']['content'], 'ok')

    def test_nonfinite_and_invalid_counts_rejected(self):
        row = stream_measurement({'prompt_tokens': True, 'completion_tokens': -7}, {'prompt_ms': float('nan')}, time.monotonic(), None, 'incomplete')
        self.assertIsNone(row['prompt_tokens'])
        self.assertIsNone(row['completion_tokens'])
        self.assertIsNone(row['prompt_ms'])

    def test_native_and_json_requests_include_usage(self):
        for protocol in ('native', 'json'):
            body = request_body([{'role': 'system', 'content': 'test'}, {'role': 'user', 'content': 'hello'}], [], 400, protocol=protocol)
            self.assertTrue(body['stream_options']['include_usage'])


class DurableMetrics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name))

    def test_counters_survive_job_roundtrip_and_count_retries(self):
        job = {'id': 'job'}
        row = {'outcome': 'complete', 'seconds': 1, 'prompt_tokens': 100, 'completion_tokens': 20}
        record_generation(job, self.ws, row, 'r1', step=1, protocol='native', estimated_input=99)
        job = json.loads(json.dumps(job))
        record_generation(job, self.ws, {**row, 'outcome': 'length'}, 'r2', step=2, protocol='json', estimated_input=99)
        record_generation(job, self.ws, {**row, 'outcome': 'incomplete', 'prompt_tokens': None, 'completion_tokens': None}, 'r3', step=2, protocol='json', estimated_input=99)
        totals = job['token_usage']
        self.assertEqual((totals['requests'], totals['measured_requests'], totals['incomplete_requests']), (3, 2, 2))
        self.assertEqual((totals['prompt_tokens'], totals['completion_tokens']), (200, 40))
        self.assertEqual(len((self.ws.meta / 'inference.jsonl').read_text(encoding='utf-8').splitlines()), 3)

    def test_logging_failure_does_not_fail_generation(self):
        job = {'id': 'j'}
        (self.ws.meta / 'inference.jsonl').mkdir()
        record_generation(job, self.ws, {'outcome': 'complete', 'seconds': 1}, 'r', step=1, protocol='native', estimated_input=99)
        self.assertIn('metrics_warning', job)
        self.assertEqual(job['token_usage']['requests'], 1)


class ExactToolCounting(unittest.TestCase):
    def test_token_count_uses_selected_catalog_not_global_catalog(self):
        import project_studio
        template = Mock()
        template.json.return_value = {'prompt': 'rendered'}
        tokenize = Mock()
        tokenize.json.return_value = {'tokens': [1, 2, 3]}
        with patch.object(project_studio.requests, 'post', side_effect=[template, tokenize]) as post:
            count = project_studio.token_count([{'role': 'user', 'content': 'hi'}], [])
        self.assertEqual(count, 3)
        self.assertEqual(post.call_args_list[0].kwargs['json']['tools'], [])


if __name__ == '__main__':
    unittest.main()
