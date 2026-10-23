from pathlib import Path
import json
import tempfile
import unittest

from project_core import Workspace
from project_quality import delivery_evidence
from project_recovery import Recovery, RecoveryReview
from project_validation import VERSION


class DeliveryEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'app.py').write_text('answer = 42\n', encoding='utf-8')
        self.ws = Workspace(self.root)
        self.report = {'success': True, 'fingerprint': self.ws.fingerprint(force=True),
                       'source_unchanged_during_validation': True, 'validator_version': VERSION,
                       'checks': [{'name': 'unit_tests', 'passed': True, 'tests': 7}]}

    def test_passing_checks_are_not_full_requirement_coverage(self):
        value = delivery_evidence(self.ws, 'Build an Arabic application', self.report)
        self.assertTrue(value['validated'])
        self.assertIn('Not inferred', value['requirements_coverage'])
        self.assertFalse(value['requirements_review_requested'])

    def test_changed_sources_cannot_keep_green_delivery(self):
        (self.root / 'app.py').write_text('answer = 43\n', encoding='utf-8')
        value = delivery_evidence(self.ws, 'request', self.report)
        self.assertFalse(value['validated'])

    def test_empty_or_failed_checks_do_not_prove_delivery(self):
        for report in (None, {**self.report, 'checks': []},
                       {**self.report, 'checks': [{'name': 'test', 'passed': False}]},
                       {**self.report, 'in_progress': True}, {**self.report, 'validator_version': -1}):
            with self.subTest(report=report):
                self.assertFalse(delivery_evidence(self.ws, 'request', report)['validated'])


class LedgerRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name))
        self.recovery = Recovery(self.ws)
        self.state = {'run_id': 'run', 'job': {'id': 'job', 'token_usage': {'requests': 2}},
                      'agent_state': {'read_receipts': {'file.py': 'sha'},
                                      'read_coverage': {'file.py': {'sha256': 'sha', 'complete': False, 'spans': [[0, 10]]}}}}

    def test_completed_mutation_not_replayed_after_resume(self):
        self.assertIsNone(self.recovery.begin(self.state, 'call', 'patch_file', {'path': 'file.py'}))
        self.recovery.finish(self.state, 'call', {'saved': 'file.py'})
        restored = Recovery(self.ws)
        self.assertEqual(restored.load('run'), self.state)
        self.assertEqual(restored.begin(self.state, 'call', 'patch_file', {'path': 'file.py'}), {'saved': 'file.py'})

    def test_uncertain_mutation_requires_review_and_is_not_replayed(self):
        self.recovery.begin(self.state, 'call', 'run_command', {'argv': ['python', 'app.py']})
        with self.assertRaises(RecoveryReview):
            Recovery(self.ws).begin(self.state, 'call', 'run_command', {'argv': ['python', 'app.py']})

    def test_call_id_conflict_rejected(self):
        self.recovery.begin(self.state, 'call', 'read_file', {'path': 'a.py'})
        self.recovery.finish(self.state, 'call', {'content': 'a'})
        with self.assertRaises(RecoveryReview):
            self.recovery.begin(self.state, 'call', 'write_file', {'path': 'a.py'})

    def test_context_projection_does_not_shorten_durable_mutations(self):
        from project_context import project_messages
        source = 'export const data = ' + json.dumps(list(range(400))) + ';'
        messages = []
        for index in range(4):
            arguments = {'path': f'{index}.js', 'content': source}
            messages.extend([{'role': 'assistant', 'tool_calls': [{'id': str(index), 'function': {'name': 'write_file', 'arguments': json.dumps(arguments)}}]},
                             {'role': 'tool', 'tool_call_id': str(index), 'content': json.dumps({'saved': f'{index}.js', 'sha256': 'sha'})}])
        self.state['agent_state']['messages'] = messages
        projected = project_messages(messages)
        self.assertIn('Older committed source omitted', projected[0]['tool_calls'][0]['function']['arguments'])
        self.recovery.save(self.state)
        loaded = Recovery(self.ws).load('run')
        self.assertEqual(json.loads(loaded['agent_state']['messages'][0]['tool_calls'][0]['function']['arguments'])['content'], source)


if __name__ == '__main__':
    unittest.main()
