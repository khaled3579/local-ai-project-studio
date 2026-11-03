import json,tempfile,unittest
from pathlib import Path
from project_core import Workspace
from project_progress import observe
from project_acceptance import host_evidence,host_only_requirements
from project_source_guard import bind_baseline

class TargetedPatchEvidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(Path(self.temp.name));self.ws.write('app.py','VALUE = 1\n')
        self.ws.write('tests/test_app.py','VALUE = 1\n')
        base=self.ws.history.checkpoint('fixture',force=True)
        self.ws.source_guard_baseline=bind_baseline(self.ws,base['id'])
        self.job={'id':'job','base_version':base['id']}

    def proof(self):return host_evidence(self.ws,{},self.job)['host:targeted_source_patch']

    def test_real_patch_is_bound_to_existing_source_and_survives_checkpoint_json(self):
        before=self.ws.fingerprint(force=True);r=self.ws.patch('app.py','VALUE = 1','VALUE = 2',self.ws.read('app.py')['sha256'])
        observe(self.job,'patch_file',{'path':'app.py'},r,before,self.ws.fingerprint(force=True))
        self.job=json.loads(json.dumps(self.job));self.assertTrue(self.proof()['passed'])
        self.ws.write('app.py','VALUE = 3\n',self.ws.read('app.py')['sha256'])
        self.assertFalse(self.proof()['passed'])
        before=self.ws.fingerprint(force=True)
        result=self.ws.patch('tests/test_app.py','VALUE = 1','VALUE = 2',self.ws.read('tests/test_app.py')['sha256'])
        observe(self.job,'patch_file',{'path':'tests/test_app.py'},result,before,self.ws.fingerprint(force=True))
        self.assertFalse(self.proof()['passed'])

    def test_noop_model_claim_new_file_and_test_only_patch_cannot_prove_constraint(self):
        self.job['targeted_patch_used']=True;self.assertFalse(self.proof()['passed'])
        before=self.ws.fingerprint(force=True);r=self.ws.write('new.py','VALUE = 1\n')
        observe(self.job,'patch_file',{'path':'new.py'},r,before,self.ws.fingerprint(force=True))
        self.assertFalse(self.proof()['passed'])

    def test_constraint_recognition_does_not_drop_mixed_behavior(self):
        self.assertEqual(host_only_requirements([{'id':'R1','text':'Use a targeted patch to change the existing implementation.'}]),{'R1':['host:targeted_source_patch']})
        self.assertEqual(host_only_requirements([{'id':'R1','text':'Use a targeted patch and add authentication.'}]),{})
