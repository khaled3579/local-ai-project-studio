import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from app_package import build, FIXED


class ReleasePackage(unittest.TestCase):
    def test_launchers_and_new_modules_shipped_without_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'source'
            root.mkdir()
            for name in FIXED:
                (root / name).write_text('# fixture\n', encoding='utf-8')
            (root / 'project_file_context.py').write_text('VALUE = 1\n', encoding='utf-8')
            (root / 'project_source_index.py').write_text('VALUE = 2\n', encoding='utf-8')
            (root / 'project_source_index.js').write_text('const value = 2;\n', encoding='utf-8')
            (root / 'daily').mkdir()
            (root / 'daily/settings.json').write_text('private settings', encoding='utf-8')
            (root / '.local-ai').mkdir()
            (root / '.local-ai/conversation.json').write_text('private chat', encoding='utf-8')
            (root / 'tests').mkdir()
            (root / 'tests/test_private.py').write_text('# private\n', encoding='utf-8')
            archive = base / 'release.zip'
            build(root, archive, '4.5.0')
            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
                for name in ('Start.cmd', 'Stop.cmd', 'Start-All.ps1', 'Stop-All.ps1', 'README_START_AR.txt', 'project_file_context.py', 'project_source_index.py', 'project_source_index.js'):
                    self.assertIn(name, names)
                self.assertFalse(any(n.startswith(('daily/', '.local-ai/', 'tests/')) for n in names))
                manifest = json.loads(package.read('APP-MANIFEST.json'))
                for name, entry in manifest['files'].items():
                    data = package.read(name)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                    self.assertEqual(len(data), entry['bytes'])


if __name__ == '__main__':
    unittest.main()
