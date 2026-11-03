import copy,json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from project_core import Workspace
from project_recovery import Recovery,RecoveryReview
import project_progress as P


class ProgressAccounting(unittest.TestCase):
    def setUp(self):
        self.job={'id':'job','started':time.time()}
        P.update_requirements(self.job,[{'id':'R1'},{'id':'R2'}])

    def test_reads_are_not_progress_and_same_source_repeats_are_visible(self):
        for _ in range(2):P.observe(self.job,'read_file',{'path':'a.py'},{'content':'a'},'fp','fp')
        state=P.tracker(self.job)
        self.assertEqual(state['progress_events'],0);self.assertEqual(state['repeat_candidates'],1)
        self.assertEqual(state['next_step']['requirement_id'],'R1')
        P.observe(self.job,'read_file',{'path':'a.py'},{'content':'a'},'fp','fp',epoch=1)
        self.assertEqual(state['repeat_candidates'],1)

    def test_source_change_invalidates_completion_until_current_proof(self):
        coverage={'validation_current':True,'items':[{'passed':True,'requirement_ids':['R1']}]}
        P.update_requirements(self.job,[{'id':'R1'},{'id':'R2'}],coverage,'old')
        P.observe(self.job,'patch_file',{}, {'saved':'a.py'},'old','new')
        self.assertEqual(P.tracker(self.job)['completed_requirement_ids'],[])
        self.assertIsNone(P.metrics(self.job)['first_useful_edit_seconds'])
        P.update_requirements(self.job,[{'id':'R1'},{'id':'R2'}],coverage,'new')
        self.assertIsNotNone(P.metrics(self.job)['first_useful_edit_seconds'])

    def test_new_test_receipt_is_progress_once_not_every_validation_run(self):
        report={'success':True,'fingerprint':'fp','source_unchanged_during_validation':True,'validator_version':7,
                'checks':[{'passed':True,'validation_run_id':'1','executed':1,'successful_tests':['python:test_x.Case.test_x']}]}
        first=P.observe(self.job,'validate_project',{},report,'fp','fp')
        report['checks'][0]['validation_run_id']='2'
        second=P.observe(self.job,'validate_project',{},report,'fp','fp')
        self.assertIn('new_valid_evidence',first['progress']);self.assertEqual(second['progress'],[])

    def test_stale_browser_or_stale_requirement_coverage_cannot_be_progress(self):
        row=P.observe(self.job,'browser_project',{'action':'assert'}, {'assertion':{'passed':True}},'new','new',browser_fingerprint='old')
        self.assertEqual(row['progress'],[])
        P.update_requirements(self.job,[{'id':'R1'}],{'validation_current':False,'items':[{'passed':True,'requirement_ids':['R1']}]},'new')
        self.assertEqual(P.tracker(self.job)['completed_requirement_ids'],[])

    def test_protocol_and_transport_recovery_do_not_instruct_source_repair(self):
        P.generation_outcome(self.job,{'outcome':'connection_error'})
        self.assertIn('restore connection',P.continuation(self.job)['next_step']['action'])
        P.generation_outcome(self.job,{'outcome':'complete'})
        self.assertEqual(P.tracker(self.job)['pending_recovery'],'transport')
        P.valid_action(self.job)
        P.observe(self.job,'generation_loop',{}, {'error':'bad envelope'},'fp','fp')
        self.assertIn('envelope',P.continuation(self.job)['next_step']['action'])
        self.assertEqual(P.tracker(self.job)['progress_events'],0)

    def test_recovery_failures_remain_and_only_actual_progress_resolves_project_lane(self):
        for _ in range(2):P.observe(self.job,'validate_project',{}, {'success':False},'fp','fp')
        P.observe(self.job,'read_file',{}, {},'fp','fp')
        self.assertEqual(P.tracker(self.job)['pending_recovery'],'project')
        P.observe(self.job,'patch_file',{}, {'saved':'a.py'},'fp','new')
        state=P.tracker(self.job)
        self.assertEqual(state['recovery_repeated_failures']['project'],1)
        self.assertEqual(state['recovery_resolved_after_progress'].get('project',0),0)
        self.assertEqual(state['pending_recovery'],'project')
        self.assertEqual(len(state['history']),4)

    def test_simultaneous_failures_remain_and_valid_action_needs_a_new_completed_stream(self):
        P.generation_outcome(self.job,{'outcome':'connection_error'})
        P.observe(self.job,'validate_project',{}, {'success':False},'fp','fp')
        P.valid_action(self.job)
        self.assertEqual(set(P.continuation(self.job)['pending_recoveries']),{'transport','project'})
        P.generation_outcome(self.job,{'outcome':'complete'})
        P.valid_action(self.job)
        self.assertEqual(P.continuation(self.job)['pending_recoveries'],['project'])

    def test_first_useful_edit_is_not_an_earlier_unvalidated_change(self):
        with patch.object(P.time,'time',return_value=10):
            P.observe(self.job,'patch_file',{}, {'saved':'a.py'},'initial','bad')
        with patch.object(P.time,'time',return_value=20):
            P.observe(self.job,'patch_file',{}, {'saved':'a.py'},'bad','good')
        P.update_requirements(self.job,[{'id':'R1'}],{'validation_current':True,'items':[{'passed':True,'requirement_ids':['R1']}]},'good')
        self.assertEqual(P.tracker(self.job)['first_useful_edit_at'],20)

    def test_unknown_request_count_stays_unknown_after_requirement_completion(self):
        P.update_requirements(self.job,[{'id':'R1'}],{'validation_current':True,'items':[{'passed':True,'requirement_ids':['R1']}]},'fp')
        self.assertIsNone(P.metrics(self.job)['requests_per_completed_requirement'])

    def test_unavailable_denominator_and_usage_are_not_reported_as_savings(self):
        self.assertIsNone(P.metrics(self.job)['requests_per_completed_requirement'])
        self.job['token_usage']={'requests':2,'measured_requests':1,'prompt_tokens':100,'completion_tokens':20}
        P.update_requirements(self.job,[{'id':'R1'}],{'validation_current':True,'items':[{'passed':True,'requirement_ids':['R1']}]},'fp')
        values=P.metrics(self.job)
        self.assertEqual(values['requests_per_completed_requirement'],2)
        self.assertIsNone(values['tokens_per_completed_requirement']);self.assertIsNone(values['measured_savings'])

    def test_continuation_survives_json_and_retains_action_reason(self):
        P.observe(self.job,'read_file',{}, {},'fp','fp')
        restored=json.loads(json.dumps(self.job))
        self.assertEqual(P.continuation(restored),P.continuation(self.job))
        self.assertTrue(P.continuation(restored)['next_step']['reason'])


class ReceiptAndReadReuse(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(Path(self.temp.name));(self.ws.root/'a.py').write_text('x=1\n',newline='\n')
        self.recovery=Recovery(self.ws)
        self.state={'run_id':'run','job':{'id':'job'},'fingerprint':self.ws.fingerprint(force=True),'execution_context':'env1'}

    def test_three_identical_reads_execute_one_backend_read(self):
        current={};invoke=Mock(side_effect=lambda:self.ws.read('a.py'))
        values=[P.cached_read(current,self.ws,'read_file',{'path':'a.py'},invoke,0) for _ in range(3)]
        self.assertEqual(invoke.call_count,1)
        self.assertEqual(values[0]['content'],values[2]['content']);self.assertTrue(values[2]['reused_read'])
        self.assertEqual(P.cached_read(json.loads(json.dumps(current)),self.ws,'read_file',{'path':'a.py'},invoke,0)['content'],'x=1\n')
        self.assertEqual(invoke.call_count,1)
        (self.ws.root/'a.py').write_text('x=2\n',newline='\n')
        self.assertEqual(P.cached_read(current,self.ws,'read_file',{'path':'a.py'},invoke,0)['content'],'x=2\n')
        self.assertEqual(invoke.call_count,2)
        P.cached_read(current,self.ws,'read_file',{'path':'a.py'},invoke,1)
        self.assertEqual(invoke.call_count,3)

    def test_scoped_completed_command_is_reused_under_new_id(self):
        args={'argv':['python','a.py']}
        self.recovery.begin(self.state,'one','run_command',args)
        self.recovery.finish(self.state,'one',{'exit_code':0,'stdout':'done'})
        result=self.recovery.begin(self.state,'two','run_command',args)
        self.assertTrue(result['reused_execution']);self.assertEqual(result['stdout'],'done')
        changed={**self.state,'execution_context':'env2'}
        self.assertIsNone(self.recovery.begin(changed,'three','run_command',args))

    def test_uncertain_command_is_never_replayed_even_under_new_id_or_environment(self):
        args={'argv':['python','a.py']}
        self.recovery.begin(self.state,'one','run_command',args)
        changed={**self.state,'execution_context':'env2'}
        with self.assertRaises(RecoveryReview):self.recovery.begin(changed,'two','run_command',args)
        self.assertEqual(len(self.recovery.calls('run')),1)

    def test_legacy_unscoped_completed_command_requires_review_not_execution(self):
        old={k:v for k,v in self.state.items() if k!='execution_context'}
        args={'argv':['python','a.py']}
        self.recovery.begin(old,'one','run_command',args)
        self.recovery.finish(old,'one',{'exit_code':0})
        with self.assertRaises(RecoveryReview):self.recovery.begin(self.state,'two','run_command',args)

    def test_environment_identity_is_hashed_and_changes_when_environment_changes(self):
        with patch.dict('os.environ',{'PROGRESS_TEST_KEY':'first-secret'}):first=P.execution_context(self.ws)
        with patch.dict('os.environ',{'PROGRESS_TEST_KEY':'second-secret'}):second=P.execution_context(self.ws)
        self.assertNotEqual(first,second);self.assertEqual(len(first),64)
        self.assertNotIn('secret',first)
