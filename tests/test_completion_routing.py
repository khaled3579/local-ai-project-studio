import tempfile,time,unittest
from pathlib import Path
from project_studio import Studio
from project_acceptance import Acceptance
from project_validation import VERSION
from project_completion import ready,restrict
from project_generation import request_body

class CompletionRouting(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name);root=folder/'project';root.mkdir()
        (root/'app.py').write_text('def counter(): return 1\n')
        self.studio=Studio(settings_path=folder/'settings.json');self.studio.select(str(root))
        self.ws=self.studio.workspace;self.req=self.studio.requirements();self.req.record('Add counter','session','one')
        catalog=self.req.catalog();row=catalog['source_requirements'][0]
        self.req.review(expected_revision=catalog['revision'],items=[{'requirement_id':row['id'],'reviewed_atomic':True,
            'segments':[{'start':0,'end':len(row['text']),'evidence_kind':'unit'}]}])
        self.job={'id':'job','started':time.time()};self.state={'acceptance_initial_tests':{}}
        self.report={'success':True,'fingerprint':self.ws.fingerprint(force=True),'source_unchanged_during_validation':True,
            'validator_version':VERSION,'checks':[{'name':'fixture','passed':True,'executed':1,'validation_run_id':'fixture',
                'successful_tests':['python:test_app.Counter.test_counter']}]}
        self.ws._save_validation_report(self.report)
        catalog=self.req.catalog();acceptance=Acceptance(self.ws,'Add counter',catalog=catalog)
        acceptance.invoke(items=[{'requirement_ids':[catalog['requirements'][0]['id']],
            'criterion':'Counter result is one','tests':['python:test_app.Counter.test_counter']}])

    def test_only_current_complete_coverage_enters_final_response_mode(self):
        self.assertIsNotNone(ready(self.studio,self.ws,self.job,'Add counter',self.state))
        (self.ws.root/'app.py').write_text('def counter(): return 2\n')
        self.assertIsNone(ready(self.studio,self.ws,self.job,'continue',self.state))

    def test_execution_environment_change_reopens_tools_before_final_response(self):
        from project_progress import execution_context
        from unittest.mock import patch
        self.state['validation_reuse_receipt']={'environment':execution_context(self.ws)}
        self.assertIsNotNone(ready(self.studio,self.ws,self.job,'Add counter',self.state))
        with patch.dict('os.environ',{'COMPLETION_FIXTURE':'different'}):
            self.assertIsNone(ready(self.studio,self.ws,self.job,'Add counter',self.state))

    def test_missing_requirement_or_invalid_final_response_reopens_tools(self):
        self.state['finalization_repair_required']=True
        self.assertIsNone(ready(self.studio,self.ws,self.job,'Add counter',self.state))
        self.state.clear();self.req.record('Add search','session','two')
        self.assertIsNone(ready(self.studio,self.ws,self.job,'continue',self.state))

    def test_missing_launch_or_stale_export_does_not_enter_final_mode(self):
        self.job['run_entry']='app.py'
        self.assertIsNone(ready(self.studio,self.ws,self.job,'Add counter',self.state))
        self.job.pop('run_entry');self.job['export']={'source_fingerprint':'old'}
        self.assertIsNone(ready(self.studio,self.ws,self.job,'Add counter',self.state))

    def test_native_final_mode_removes_tools_without_weakening_delivery_gate(self):
        body=restrict({'messages':[],'tools':[{'x':1}],'parallel_tool_calls':False},'native',{})
        self.assertEqual(body['tool_choice'],'none');self.assertNotIn('tools',body)
        self.assertIn('independently validate',body['messages'][-1]['content'])

    def test_json_final_mode_schema_allows_only_final(self):
        body=request_body([{'role':'system','content':'test'},{'role':'user','content':'report'}],[],1000,protocol='json')
        variants=body['response_format']['json_schema']['schema']['oneOf']
        self.assertEqual(len(variants),1);self.assertEqual(variants[0]['properties']['tool']['const'],'final')
