"""Exercise production controller + SSE + tools + recovery with a scripted model.

These are integration regressions, not model-quality measurements.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import project_studio
from project_validation import VERSION
from test_inference_metrics import Response


class StudioIntegration(unittest.TestCase):
    def run_controller(self, protocol, unsafe_write=False, premature_final=False, autonomous=False):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            root = directory / 'project'
            root.mkdir()
            (root / 'app.py').write_text('def add(a, b):\n    return a - b\n\nUNRELATED = 42\n', encoding='utf-8', newline='\n')
            studio = project_studio.Studio(settings_path=directory / 'settings.json')
            studio.select(str(root))
            (studio.workspace.meta / 'repair-settings.json').write_text(json.dumps({'autonomous_repair':autonomous}), encoding='utf-8')
            prompt = 'Fix add in app.py and preserve unrelated code.'
            studio.job = {'id': 'integration-job', 'status': 'running', 'mode': 'project', 'original_prompt': prompt,
                          'prompt': prompt, 'started': 1, 'verified': False}
            studio.repair_run = {'id': 'integration-run', 'status': 'running', 'attempt_index': 1}
            actions = [
                ('read_file', {'path': 'app.py', 'symbol': 'add'}),
                ('patch_file', {'path': 'app.py', 'old_text': 'return a - b', 'new_text': 'return a + b', 'expected_sha256': '@last_read'}),
                ('validate_project', {}),
                ('acceptance_plan', {'items': [{'requirement_ids': ['R1'], 'criterion': 'Addition handles signed operands and preserves unrelated code', 'tests': ['python:test_app.Add.test_add']}]}), (None, {})]
            if unsafe_write:
                actions.insert(0, ('write_file', {'path': 'app.py', 'content': 'UNRELATED = "destroyed"\n',
                                                  'expected_sha256': studio.workspace.read('app.py')['sha256']}))
            if premature_final:
                actions.insert(-2, (None, {}))
            sent = []

            def post(url, **kwargs):
                if url.endswith('/apply-template'):
                    r = Mock(); r.json.return_value = {'prompt': 'template'}; return r
                if url.endswith('/tokenize'):
                    r = Mock(); r.json.return_value = {'tokens': [123]}; return r
                body = kwargs['json']; sent.append(body)
                name, arguments = actions[len(sent) - 1]
                if protocol == 'json':
                    content = json.dumps({'tool': name, 'arguments': arguments} if name else {'tool': 'final', 'message': 'تم إصلاح `app.py` والتحقق منه.'})
                    delta, finish = {'content': content}, 'stop'
                elif name:
                    delta = {'tool_calls': [{'index': 0, 'id': f'c{len(sent)}', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                    finish = 'tool_calls'
                else:
                    delta, finish = {'content': 'تم إصلاح `app.py` والتحقق منه.'}, 'stop'
                response = Response([{'choices': [{'delta': delta, 'finish_reason': finish}]},
                                     {'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}, '[DONE]'])
                response.status_code = 200
                return response

            def validate():
                report = {'success': True, 'source_unchanged_during_validation': True,
                          'fingerprint': studio.workspace.fingerprint(force=True), 'validator_version': VERSION,
                          'checks': [{'name': 'integration_fixture', 'passed': True, 'tests': 1, 'executed': 1, 'validation_run_id': 'fixture-run', 'successful_tests': ['python:test_app.Add.test_add']}]}
                studio.workspace._save_validation_report(report)
                return report

            manager = Mock()
            manager.json.return_value = {'actual_context': 32768, 'active': {'model': 'gemma4-unit' if protocol == 'json' else 'qwen-unit'}}
            with patch.object(project_studio.requests, 'post', side_effect=post), \
                 patch.object(project_studio.requests, 'get', return_value=manager), \
                 patch.object(studio.workspace, 'validate', side_effect=validate):
                studio.agent_attempt(prompt)
            self.assertEqual(studio.job['status'], 'complete', studio.job)
            self.assertTrue(studio.job['verified'])
            expected_requests = len(actions)
            self.assertEqual(studio.job['token_usage']['requests'], expected_requests)
            self.assertEqual(studio.job['token_usage']['prompt_tokens'], 100 * expected_requests)
            self.assertEqual(studio.job['token_usage']['completion_tokens'], 20 * expected_requests)
            self.assertIn('return a + b', (root / 'app.py').read_text(encoding='utf-8'))
            self.assertIn('UNRELATED = 42', (root / 'app.py').read_text(encoding='utf-8'))
            self.assertTrue(json.loads((studio.workspace.meta / 'delivery-evidence.json').read_text(encoding='utf-8'))['validated'])
            self.assertEqual(len(studio.recovery().calls('integration-run')), 6 if premature_final else 5)  # read, patch, tool validation, final validation
            saved = studio.recovery().load('integration-run')
            self.assertFalse(saved['agent_state']['read_coverage']['app.py']['complete'])
            self.assertTrue(all(body['stream_options']['include_usage'] for body in sent))
            self.assertNotIn('Mandatory final requirements review:', json.dumps(sent))

    def test_native_autonomous_complete_mapping_needs_no_extra_review(self):
        self.run_controller('native', autonomous=True)

    def test_json_autonomous_missing_plan_repairs_only_mapping(self):
        self.run_controller('json', autonomous=True, premature_final=True)

    def test_native_premature_delivery_is_blocked_until_mapping(self):
        self.run_controller('native', premature_final=True)

    def test_json_premature_delivery_is_blocked_until_mapping(self):
        self.run_controller('json', premature_final=True)

    def test_native_controller_preserves_files_and_durable_state(self):
        self.run_controller('native')

    def test_json_controller_preserves_files_and_durable_state(self):
        self.run_controller('json')

    def test_native_controller_rejects_unread_whole_file_replacement(self):
        self.run_controller('native', unsafe_write=True)

    def test_json_controller_rejects_unread_whole_file_replacement(self):
        self.run_controller('json', unsafe_write=True)


if __name__ == '__main__':
    unittest.main()
