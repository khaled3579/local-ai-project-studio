"""Offline, hash-locked Python environment bootstrap and installation diagnostics."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
from app_maintenance import atomic_json, contained, digest, exclusive

try:VERSION=json.loads((Path(__file__).resolve().parent/'release.json').read_text(encoding='utf-8'))['version']
except (OSError,ValueError,KeyError):VERSION='4.0.0'
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0


def lock(root):
    path=Path(root)/'runtime-lock.json';value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('version')!=1:raise ValueError('قفل الاعتمادات غير مدعوم')
    return value,digest(path)


def probe(root):
    value,sha=lock(root)
    import requests,psutil,PIL,pypdf
    actual={name:importlib.metadata.version(name) for name in value['packages']}
    if actual!=value['packages']:raise ValueError('إصدارات الاعتمادات لا تطابق القفل')
    if sys.version_info[:2]!=(3,13) or platform.architecture()[0]!='64bit':raise ValueError('يلزم Python 3.13 بنواة 64-bit')
    return {'success':True,'python':sys.executable,'python_version':platform.python_version(),'lock_sha256':sha,'packages':actual,
            'isolated':sys.prefix!=sys.base_prefix,'pypdf_path':pypdf.__file__}


def run_probe(root,python):
    result=subprocess.run([str(python),'-I',str(Path(root)/'app_runtime.py'),'--root',str(root),'probe'],capture_output=True,text=True,encoding='utf-8',timeout=30,creationflags=FLAGS)
    if result.returncode:raise RuntimeError('فشل فحص بيئة التشغيل: '+result.stderr[-2000:])
    value=json.loads(result.stdout)
    if not value['isolated']:raise ValueError('بيئة التشغيل ليست معزولة')
    return value


def prepare(root,activate=True):
    root=Path(root).resolve();value,sha=lock(root)
    if sys.version_info[:2]!=(3,13) or platform.architecture()[0]!='64bit':raise ValueError('ثبّت Python 3.13 x64 لتجهيز الحزمة')
    environment=contained(root,'.environments/'+sha[:20]);python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    marker=environment/'verified.json'
    # A per-environment claim prevents simultaneous launchers from installing over each other.
    claim=contained(root,'.environment-setup.lock')
    with exclusive(claim):
        if not marker.exists():
            for wheel,expected in value['wheels'].items():
                if digest(contained(root,'wheelhouse/'+wheel))!=expected:raise ValueError('بصمة حزمة Python غير مطابقة: '+wheel)
            if digest(root/'requirements.lock.txt')!=value['requirements_sha256']:raise ValueError('ملف متطلبات غير مطابق للقفل')
            if not python.exists():
                result=subprocess.run([sys.executable,'-I','-m','venv',str(environment)],capture_output=True,text=True,timeout=120,creationflags=FLAGS)
                if result.returncode:raise RuntimeError(result.stderr[-2000:])
            result=subprocess.run([str(python),'-I','-m','pip','install','--no-index','--find-links',str(root/'wheelhouse'),'--only-binary=:all:','--require-hashes','-r',str(root/'requirements.lock.txt')],capture_output=True,text=True,encoding='utf-8',timeout=180,creationflags=FLAGS)
            if result.returncode:raise RuntimeError('تعذر تثبيت الاعتمادات دون اتصال: '+result.stderr[-2500:])
        checked=run_probe(root,python);atomic_json(marker,checked)
        if activate:atomic_json(root/'daily/runtime.json',{'version':1,'environment':environment.relative_to(root).as_posix(),'lock_sha256':sha,'release':json.loads((root/'release.json').read_text(encoding='utf-8'))['version'] if (root/'release.json').exists() else VERSION})
        return {'python':str(python),'environment':str(environment),'verified':checked}


def selected(root):
    root=Path(root);value,sha=lock(root);environment=contained(root,'.environments/'+sha[:20])
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():raise ValueError('بيئة التشغيل لم تُجهّز بعد؛ شغّل Prepare-Runtime.ps1')
    return python


def self_test(root):
    root=Path(root).resolve();sys.path.insert(0,str(root))
    if sys.prefix==sys.base_prefix:raise ValueError('Self-test requires the isolated runtime')
    import project_core,project_studio,project_attachments,project_recovery,project_validation,project_workbench
    capability=subprocess.run([str(root/'runtime/node/node.exe'),str(root/'project_assistant_health.js'),'--full'],capture_output=True,text=True,encoding='utf-8',timeout=35,creationflags=FLAGS)
    if capability.returncode:raise ValueError('Assistant tools health failed: '+capability.stderr[-2000:])
    from PIL import Image
    import io
    image=Image.new('RGB',(2,2),'green');raw=io.BytesIO();image.save(raw,format='PNG');Image.open(io.BytesIO(raw.getvalue())).verify()
    return {'success':True,'version':VERSION,'validation_version':project_validation.VERSION,'imports':True,'image_roundtrip':True,'dependencies':probe(root),'assistant_tools':json.loads(capability.stdout)}


def doctor(root,full=False):
    root=Path(root).resolve();checks=[]
    try:python=selected(root);checks.append({'name':'python_environment','passed':True,'details':run_probe(root,python)})
    except Exception as e:checks.append({'name':'python_environment','passed':False,'error':str(e)})
    environment=os.environ.copy();environment.pop('PYTHONPATH',None);environment.pop('PYTHONHOME',None)
    if environment.get('CUDA_PATH'):environment['PATH']=str(Path(environment['CUDA_PATH'])/'bin')+os.pathsep+environment.get('PATH','')
    for name,executable,args in [('node',root/'runtime/node/node.exe',['--version']),('assistant_tools',root/'runtime/node/node.exe',[str(root/'project_assistant_health.js'),'--full']),('llama_runtime',root/'runtime/llama-server.exe',['--version'])]:
        if not full and name=='llama_runtime':continue
        try:
            result=subprocess.run([str(executable),*args],env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,creationflags=FLAGS)
            checks.append({'name':name,'passed':result.returncode==0,'output':(result.stdout+result.stderr)[-3000:]})
        except Exception as e:checks.append({'name':name,'passed':False,'error':str(e)})
    return {'version':VERSION,'root':str(root),'success':all(c['passed'] for c in checks),'checks':checks,
            'requirements':['Python 3.13 x64 installed','NVIDIA driver and CUDA runtime for GPU inference','model weights selected by the user; not in the package']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(Path(__file__).resolve().parent));parser.add_argument('command',choices=['prepare','probe','self-test','doctor']);args=parser.parse_args()
    if args.command=='prepare':value=prepare(args.root)
    elif args.command=='probe':value=probe(args.root)
    elif args.command=='self-test':value=self_test(args.root)
    else:value=doctor(args.root,True)
    print(json.dumps(value,ensure_ascii=False));return 0 if value.get('success',True) else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print(str(error),file=sys.stderr);sys.exit(1)
