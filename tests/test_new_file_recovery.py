import json,tempfile,unittest
from pathlib import Path
from project_core import Workspace
from project_generation import remember_new_file_recovery,action_format_feedback,enforce_patch_recovery

class NewFileRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(Path(self.temp.name));self.state={}
        self.prefix='{"tool":"write_file","arguments":{"path":"database.py","content":"'

    def test_rejected_large_creation_records_only_path_and_never_executes_source(self):
        self.assertEqual(remember_new_file_recovery(self.prefix+'secret rejected source',self.ws,self.state,size=26000),'database.py')
        self.assertFalse((self.ws.root/'database.py').exists())
        self.assertNotIn('secret',json.dumps(self.state))
        self.assertEqual(json.loads(json.dumps(self.state))['new_file_recovery_targets'],['database.py'])
        tools=[{'function':{'name':'write_file','parameters':{}}}]
        feedback=action_format_feedback('Invalid JSON',tools,new_file_targets=['database.py'])
        self.assertIn('4000',feedback);self.assertIn('not delivery',feedback)
        self.assertIn('SAME file',feedback)

    def test_guard_blocks_repeated_large_creation_then_requires_small_patches(self):
        remember_new_file_recovery(self.prefix,self.ws,self.state,size=26000)
        with self.assertRaisesRegex(ValueError,'4000'):
            enforce_patch_recovery('write_file',{'path':'database.py','content':'x'*4001},self.state,self.ws)
        enforce_patch_recovery('write_file',{'path':'database.py','content':'VALUE = 1\n'},self.state,self.ws)
        (self.ws.root/'database.py').write_text('VALUE = 1\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'patch_file'):
            enforce_patch_recovery('write_file',{'path':'database.py','content':'VALUE = 2\n'},self.state,self.ws)
        enforce_patch_recovery('patch_file',{'path':'database.py','old_text':'VALUE = 1','new_text':'VALUE = 2'},self.state,self.ws)
        with self.assertRaisesRegex(ValueError,'4000'):
            enforce_patch_recovery('patch_file',{'path':'database.py','new_text':'x'*4001},self.state,self.ws)

    def test_small_invalid_responses_existing_files_and_unsafe_paths_are_not_creation_targets(self):
        self.assertIsNone(remember_new_file_recovery(self.prefix,self.ws,self.state))
        self.assertIsNone(remember_new_file_recovery(self.prefix.replace('database.py','../escape.py'),self.ws,self.state,size=26000))
        (self.ws.root/'database.py').write_text('unchanged',encoding='utf-8')
        self.assertIsNone(remember_new_file_recovery(self.prefix,self.ws,self.state,size=26000))
        self.assertFalse(self.state.get('new_file_recovery_targets'))

    def test_unrelated_files_and_final_delivery_checks_are_not_redefined(self):
        remember_new_file_recovery(self.prefix,self.ws,self.state,size=26000)
        enforce_patch_recovery('write_file',{'path':'other.py','content':'x'*5000},self.state,self.ws)
        enforce_patch_recovery('final',{},self.state,self.ws)
        self.assertEqual(list(self.state),['new_file_recovery_targets'])
