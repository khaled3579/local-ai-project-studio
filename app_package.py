"""Build source/runtime releases without user projects, settings, chats or weights."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import zipfile
from app_maintenance import digest

FIXED={'daily_ai.py','daily_models.html','model_library.py','model_folder_picker.py','validation_python_runner.py','validation_node_runner.cjs','validation_gui_runner.py',
       'app_runtime.py','app_package.py','app_updates.py','app_maintenance.py','requirements.txt','requirements.lock.txt','runtime-lock.json',
       'Launch-Daily.ps1','Launch-Projects.ps1','Prepare-Runtime.ps1','Update-App.ps1','Rollback-App.ps1','Doctor.ps1',
       'Daily-AI.cmd','Daily-Projects.cmd','PACKAGE_README_AR.txt'}
FIXED.update({'Prepare-Tabby.ps1', 'README_ENGINES_AR.txt'})
FIXED.update({'Start.cmd', 'Stop.cmd', 'Start-All.ps1', 'Stop-All.ps1', 'README_START_AR.txt'})


def build(root,destination,version='4.0.0'):
    root=Path(root).resolve();destination=Path(destination).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    files=[root/name for name in FIXED]
    files += [p for p in root.glob('model_*') if p.suffix in {'.py','.js'}]
    files += [p for p in root.glob('project_*') if p.suffix in {'.py','.html','.js','.css'}]
    files += [p for folder in ('runtime','wheelhouse') for p in (root/folder).rglob('*') if p.is_file() and not p.is_relative_to(root/'runtime/tabbyapi') and not (folder=='runtime' and p.name=='debug.log' and p.is_relative_to(root/'runtime/browser'))]
    if any(not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root) for p in files):raise ValueError('حزمة ناقصة أو مسار غير صالح')
    release=json.dumps({'version':version,'data_schema':1,'minimum_schema':1,'created':time.time(),'platform':'Windows-x64-Python313'},indent=2).encode()
    manifest={'format':1,'release':version,'data_schema':1,'files':{p.relative_to(root).as_posix():{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(set(files))}}
    manifest['files']['release.json']={'sha256':hashlib.sha256(release).hexdigest(),'bytes':len(release)}
    temporary=destination.with_suffix('.building')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        z.writestr('APP-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        z.writestr('release.json',release)
        for p in sorted(set(files)):z.write(p,p.relative_to(root).as_posix())
    temporary.replace(destination);sha=digest(destination)
    destination.with_suffix(destination.suffix+'.sha256').write_text(sha+'  '+destination.name+'\n',encoding='ascii')
    return {'path':str(destination),'sha256':sha,'files':len(manifest['files']),'bytes':destination.stat().st_size,'release':version}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination');parser.add_argument('--root',default=str(Path(__file__).resolve().parent));parser.add_argument('--version',default='4.0.0');args=parser.parse_args();print(json.dumps(build(args.root,args.destination,args.version)))
