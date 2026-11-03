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
from scripted_model import scripted_transport, review_fixture


class StudioIntegration(unittest.TestCase):
    def run_controller(self, protocol, unsafe_write=False, premature_final=False, autonomous=False, review_in_agent=False, repeat_read=False, recover_validation=False, large_creation=None):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            root = directory / 'project'
            root.mkdir()
            (root / 'app.py').write_text('def add(a, b):\n    return a - b\n\nUNRELATED = 42\n', encoding='utf-8', newline='\n')
            studio = project_studio.Studio(settings_path=directory / 'settings.json')
            studio.select(str(root))
            (studio.workspace.meta / 'repair-settings.json').write_text(json.dumps({'autonomous_repair':autonomous}), encoding='utf-8')
            prompt = 'Fix add in app.py and preserve unrelated code.'
            studio.requirements().record(prompt, studio.session_id, 'fixture-request')
            if not review_in_agent:
                review_fixture(studio.requirements())
            requirement_ids = [row['id'] for row in studio.requirements().catalog()['requirements']]
            studio.job = {'id': 'integration-job', 'status': 'running', 'mode': 'project', 'original_prompt': prompt,
                          'prompt': prompt, 'started': 1, 'verified': False}
            studio.repair_run = {'id': 'integration-run', 'status': 'running', 'attempt_index': 1}
            actions = [
                ('read_file', {'path': 'app.py', 'symbol': 'add'}),
                ('patch_file', {'path': 'app.py', 'old_text': 'return a - b', 'new_text': 'return a + b', 'expected_sha256': '@last_read'}),
                ('validate_project', {}),
                ('acceptance_plan', {'items': [{'requirement_ids': requirement_ids, 'criterion': 'Addition handles signed operands and preserves unrelated code', 'tests': ['python:test_app.Add.test_add']}]}), (None, {})]
            if unsafe_write:
                actions.insert(0, ('write_file', {'path': 'app.py', 'content': 'UNRELATED = "destroyed"\n',
                                                  'expected_sha256': studio.workspace.read('app.py')['sha256']}))
            if premature_final:
                actions.insert(-2, (None, {}))
            if repeat_read:
                actions.insert(1,('read_file',{'path':'app.py','symbol':'add'}))
            if recover_validation:
                actions[3:3]=[('validate_project',{}) for _ in range(3)]
            if review_in_agent:
                catalog=studio.requirements().catalog()
                block=catalog['source_requirements'][0];boundary=block['text'].index(' and ')
                actions.insert(0,('review_project_requirements',{'expected_revision':catalog['revision'],'items':[{
                    'requirement_id':block['id'],'reviewed_atomic':True,'segments':[
                        {'start':0,'end':boundary,'evidence_kind':'behavior'},
                        {'start':boundary,'end':len(block['text']),'evidence_kind':'behavior'}]}]}))
            sent = []
            if large_creation:
                actions[:0]=[('write_file',{'path':'database.py','content':'VALUE = 0\n'+'# padding\n'*600}),
                             ('write_file',{'path':'database.py','content':'VALUE = 1\n'})]

            def post(url, **kwargs):
                if url.endswith('/apply-template'):
                    r = Mock(); r.json.return_value = {'prompt': 'template'}; return r
                if url.endswith('/tokenize'):
                    r = Mock(); r.json.return_value = {'tokens': [123]}; return r
                body = kwargs['json']; sent.append(body)
                name, arguments = actions[len(sent) - 1]
                if review_in_agent and name=='acceptance_plan':
                    arguments['items'][0]['requirement_ids']=[r['id'] for r in studio.requirements().catalog()['requirements']]
                if protocol == 'json':
                    content = json.dumps({'tool': name, 'arguments': arguments} if name else {'tool': 'final', 'message': 'تم إصلاح `app.py` والتحقق منه.'})
                    delta, finish = {'content': content}, 'stop'
                    if large_creation and len(sent)==1:
                        if large_creation=='invalid':delta['content']=content[:-2]
                        else:finish='length'
                elif name:
                    delta = {'tool_calls': [{'index': 0, 'id': f'c{len(sent)}', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                    finish = 'tool_calls'
                    if large_creation=='length' and len(sent)==1:finish='length'
                else:
                    delta, finish = {'content': 'تم إصلاح `app.py` والتحقق منه.'}, 'stop'
                response = Response([{'choices': [{'delta': delta, 'finish_reason': finish}]},
                                     {'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}, '[DONE]'])
                response.status_code = 200
                return response

            validations=[]
            def validate():
                validations.append(1)
                passed=not recover_validation or len(validations)>3
                report = {'success': True, 'source_unchanged_during_validation': True,
                          'fingerprint': studio.workspace.fingerprint(force=True), 'validator_version': VERSION,
                          'checks': [{'name': 'integration_fixture', 'passed': True, 'tests': 1, 'executed': 1, 'validation_run_id': 'fixture-run', 'successful_tests': ['python:test_app.Add.test_add']}]}
                if not passed:
                    report['success']=False
                    report['checks'][0].update(passed=False,successful_tests=[],error='Fixture assertion failed')
                studio.workspace._save_validation_report(report)
                return report

            with scripted_transport(directory, protocol, post), \
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
            self.assertEqual(len(studio.recovery().calls('integration-run')), (6 if premature_final else 5)+int(review_in_agent)+int(repeat_read)+3*int(recover_validation)+int(bool(large_creation)))
            if large_creation:
                self.assertEqual((root/'database.py').read_text(),'VALUE = 1\n')
                self.assertIn('starting skeleton',json.dumps(sent[1],ensure_ascii=False))
                self.assertTrue(any(e['kind']=='small_creation_recovery' for e in studio.events))
                if protocol=='native' and large_creation=='length':
                    self.assertIn('tools',sent[1])
                    self.assertNotIn('response_format',sent[1])
            saved = studio.recovery().load('integration-run')
            if large_creation:
                self.assertIn('database.py',saved['agent_state']['generation_state']['new_file_recovery_targets'])
                if protocol=='native' and large_creation=='length':
                    self.assertEqual(saved['agent_state']['generation_state']['protocol'],'native')
            self.assertFalse(saved['agent_state']['read_coverage']['app.py']['complete'])
            self.assertIn('continuation',saved['agent_state'])
            self.assertTrue(saved['agent_state']['continuation']['next_step']['reason'])
            self.assertEqual(saved['agent_state']['continuation']['remaining'],[])
            self.assertEqual(saved['agent_state']['continuation']['next_step']['action'],'complete')
            self.assertEqual(saved['kind'],'after_attempt')
            self.assertEqual(saved['job']['status'],'complete')
            if recover_validation:
                self.assertGreater(studio.job.get('plan_changes',0),0)
                self.assertEqual(studio.repair_run.get('context_transitions',[]),[])
                self.assertEqual(studio.job['efficiency']['recovery_resolved_after_progress']['project'],1)
            if repeat_read:
                self.assertEqual(studio.job['efficiency']['reused_reads'],1)
                calls=studio.recovery().calls('integration-run')
                self.assertTrue(any(json.loads(row['result']).get('reused_read') for row in calls))
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

    def test_native_reviews_compound_obligations_during_task(self):
        self.run_controller('native',review_in_agent=True)

    def test_json_reviews_compound_obligations_during_task(self):
        self.run_controller('json',review_in_agent=True)

    def test_native_reuses_identical_read_without_losing_checkpoint(self):
        self.run_controller('native',repeat_read=True)

    def test_json_reuses_identical_read_without_losing_checkpoint(self):
        self.run_controller('json',repeat_read=True)

    def test_validation_recovery_changes_plan_without_unnecessary_context_rotation(self):
        self.run_controller('native',autonomous=True,recover_validation=True)

    def test_json_invalid_large_new_file_recovers_without_executing_rejected_source(self):
        self.run_controller('json',autonomous=True,large_creation='invalid')

    def test_json_output_limited_new_file_recovers_without_partial_execution(self):
        self.run_controller('json',autonomous=True,large_creation='length')

    def test_native_output_limit_preserves_the_working_tool_protocol(self):
        self.run_controller('native',autonomous=True,large_creation='length')


if __name__ == '__main__':
    unittest.main()
