import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from project_core import Workspace
from project_validation import VERSION
from project_validation_reuse import validate

class ValidationReuse(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(Path(self.temp.name));self.ws.write('a.py','VALUE = 1\n');self.state={}
        self.invoke=Mock(side_effect=self.execute)
    def execute(self):
        report={'success':True,'validator_version':VERSION,'timestamp':time.time(),
            'fingerprint':self.ws.fingerprint(force=True),'source_unchanged_during_validation':True,
            'checks':[{'passed':True,'executed':1,'validation_run_id':str(self.invoke.call_count),'successful_tests':['python:test_a.C.test_value']}]}
        (self.ws.meta/'last_validation.json').write_text(json.dumps(report),encoding='utf-8');self.ws.validation_pending=False
        return report
    def test_repeat_reuses_receipt_across_json_restore_without_inventing_new_execution(self):
        first=validate(self.state,self.ws,{},self.invoke,1);self.state=json.loads(json.dumps(self.state))
        again=validate(self.state,self.ws,{},self.invoke,1)
        self.assertTrue(again['reused_validation']);self.assertEqual(self.invoke.call_count,1)
        self.assertEqual(first['checks'],again['checks'])
    def test_cached_validation_is_recorded_as_reuse_without_new_progress(self):
        from project_progress import observe,metrics
        first=validate(self.state,self.ws,{},self.invoke,1)
        fp=self.ws.fingerprint(force=True);job={'started':1}
        observe(job,'validate_project',{},first,fp,fp)
        cached=validate(self.state,self.ws,{},self.invoke,1)
        row=observe(job,'validate_project',{},cached,fp,fp)
        self.assertTrue(row['repeated_on_same_source']);self.assertTrue(row['reused'])
        self.assertEqual(row['progress'],[])
        self.assertEqual(job['efficiency']['repeat_candidates'],1)
        self.assertEqual(job['efficiency']['repeat_without_progress_candidates'],0)
        restored=json.loads(json.dumps(job))
        self.assertEqual(metrics(restored)['reused_validations'],1)

    def test_source_environment_failed_or_tampered_report_requires_execution(self):
        validate(self.state,self.ws,{},self.invoke,1)
        self.ws.write('a.py','VALUE = 2\n',self.ws.read('a.py')['sha256'])
        validate(self.state,self.ws,{},self.invoke,1);self.assertEqual(self.invoke.call_count,2)
        with patch.dict('os.environ',{'VALIDATION_REUSE_FIXTURE':'changed'}):validate(self.state,self.ws,{},self.invoke,1)
        self.assertEqual(self.invoke.call_count,3)
        p=self.ws.meta/'last_validation.json';r=json.loads(p.read_text());r['checks'][0]['successful_tests']=['fake'];p.write_text(json.dumps(r))
        validate(self.state,self.ws,{},self.invoke,1);self.assertEqual(self.invoke.call_count,4)
        r=json.loads(p.read_text());r['success']=False;p.write_text(json.dumps(r))
        validate(self.state,self.ws,{},self.invoke,1);self.assertEqual(self.invoke.call_count,5)
    def test_forced_repeat_has_explicit_reason_and_new_job_cannot_inherit_old_receipt(self):
        validate(self.state,self.ws,{},self.invoke,1)
        with self.assertRaises(ValueError):validate(self.state,self.ws,{'force':True},self.invoke,1)
        validate(self.state,self.ws,{'force':True,'reason':'Check changed external fixture state'},self.invoke,1)
        self.assertEqual(self.invoke.call_count,2)
        validate({},self.ws,{},self.invoke,time.time()+1);self.assertEqual(self.invoke.call_count,3)
