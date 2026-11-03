import hashlib,json,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from app_updates import install

class SourceUpdateRuntime(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'installed';self.root.mkdir()
        self.files={'app.py':b'VALUE = 1\n','app_runtime.py':b'# runtime fixture\n',
                    'runtime-lock.json':b'{"version":1}', 'requirements.lock.txt':b'# unchanged\n',
                    'release.json':b'{"version":"old","data_schema":1}'}
        for n,data in self.files.items():(self.root/n).write_bytes(data)
        (self.root/'APP-MANIFEST.json').write_text('{"previous":true}')
        (self.root/'daily').mkdir();(self.root/'daily/settings.json').write_text('private sentinel')

    def package(self,changed_lock=False,bundled=False):
        files={**self.files,'app.py':b'VALUE = 2\n','release.json':b'{"version":"new","data_schema":1}'}
        if changed_lock:files['runtime-lock.json']=b'{"version":2}'
        if bundled:files['wheelhouse/fixture.whl']=b'bundled fixture'
        manifest={'format':1,'data_schema':1,'release':'new','files':{
            n:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)} for n,data in files.items()}}
        p=self.base/'update.zip'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('APP-MANIFEST.json',json.dumps(manifest))
            for n,data in files.items():z.writestr(n,data)
        return p,hashlib.sha256(p.read_bytes()).hexdigest()

    def test_default_source_update_reuses_verified_installed_runtime(self):
        package,digest=self.package()
        with patch('app_runtime.selected',return_value=Path(sys.executable)) as selected, \
             patch('app_runtime.run_probe',return_value={'isolated':True}) as probe, \
             patch('app_runtime.prepare',return_value={'python':sys.executable}) as prepare, \
             patch('app_updates.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')):
            result=install(self.root,package,digest)
        self.assertEqual(result['installed'],'new')
        selected.assert_called_once_with(self.root);probe.assert_called_once_with(self.root,Path(sys.executable))
        prepare.assert_called_once_with(self.root)
        self.assertEqual((self.root/'daily/settings.json').read_text(),'private sentinel')
        self.assertEqual(json.loads((self.root/'.update-journal.json').read_text())['status'],'complete')

    def test_source_dependency_change_rejected_before_mutation(self):
        package,digest=self.package(changed_lock=True)
        before={p.name:p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        with patch('app_runtime.prepare') as prepare:
            with self.assertRaisesRegex(ValueError,'runtime|اعتماد'):
                install(self.root,package,digest)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir() if p.is_file()})
        prepare.assert_not_called()

    def test_unverified_installed_environment_is_rejected_before_code_mutation(self):
        package,digest=self.package()
        with patch('app_runtime.selected',return_value=Path(sys.executable)), \
             patch('app_runtime.run_probe',side_effect=ValueError('invalid installed runtime')), \
             patch('app_runtime.prepare') as prepare:
            with self.assertRaisesRegex(ValueError,'invalid installed runtime'):
                install(self.root,package,digest)
        self.assertEqual((self.root/'app.py').read_bytes(),self.files['app.py'])
        self.assertFalse((self.root/'.update-journal.json').exists())
        prepare.assert_not_called()

    def test_post_install_self_test_failure_rolls_source_back_and_preserves_data(self):
        package,digest=self.package()
        with patch('app_runtime.selected',return_value=Path(sys.executable)), \
             patch('app_runtime.run_probe',return_value={'isolated':True}), \
             patch('app_runtime.prepare',return_value={'python':sys.executable}), \
             patch('app_updates.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','injected')):
            with self.assertRaises(RuntimeError):install(self.root,package,digest)
        self.assertEqual((self.root/'app.py').read_bytes(),self.files['app.py'])
        self.assertEqual((self.root/'APP-MANIFEST.json').read_text(),'{"previous":true}')
        self.assertEqual((self.root/'daily/settings.json').read_text(),'private sentinel')
        self.assertEqual(json.loads((self.root/'.update-journal.json').read_text())['status'],'rolled_back')

    def test_bundled_update_keeps_staged_runtime_and_post_install_self_tests(self):
        package,digest=self.package(bundled=True)
        with patch('app_runtime.selected') as selected, \
             patch('app_runtime.prepare',return_value={'python':sys.executable}) as prepare, \
             patch('app_updates.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')) as checked:
            install(self.root,package,digest)
        self.assertEqual(prepare.call_count,2);self.assertEqual(checked.call_count,2)
        self.assertEqual(prepare.call_args_list[0].kwargs,{'activate':False})
        selected.assert_not_called()
