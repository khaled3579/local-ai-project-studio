"""Verified offline releases with a durable journal and automatic code rollback."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import closing
from app_maintenance import atomic_json, contained, digest, exclusive

FORBIDDEN={'daily','projects','models','wheel-cache','.local-ai','.environments','.updates','.git','python-libs'}


def release_path(root,name):
    path=PurePosixPath(name)
    if '\\' in name or not path.parts or path.parts[0].casefold() in FORBIDDEN or path.parts[0].startswith('.'):
        raise ValueError('الحزمة تحاول تغيير بيانات المستخدم أو مسارًا محظورًا')
    # Source files and the two declared dependency trees are the only release surface.
    if len(path.parts)>1 and path.parts[0] not in {'runtime','wheelhouse'}:raise ValueError('مجلد غير مسموح في الحزمة')
    if len(path.parts)==1 and path.suffix.lower() not in {'.py','.js','.cjs','.css','.html','.json','.txt','.ps1','.cmd'}:raise ValueError('نوع ملف غير مسموح')
    return contained(root,name)


def extract(package,sha,stage):
    package=Path(package);stage=Path(stage)
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha) or digest(package)!=sha:raise ValueError('بصمة ZIP غير مطابقة؛ لم يتغير التثبيت')
    with zipfile.ZipFile(package) as z:
        infos=z.infolist()
        if len(infos)>15000 or sum(i.file_size for i in infos)>2*1024**3:raise ValueError('الحزمة تتجاوز حدود الإصدار')
        names=[i.filename for i in infos]
        if len({n.casefold() for n in names})!=len(names):raise ValueError('مسارات مكررة في ZIP')
        if z.getinfo('APP-MANIFEST.json').file_size>4*1024*1024:raise ValueError('Manifest too large')
        if shutil.disk_usage(stage.parent).free<sum(i.file_size for i in infos)*3+64*1024*1024:raise OSError('Insufficient staging space')
        manifest=json.loads(z.read('APP-MANIFEST.json'))
        if manifest.get('format')!=1 or manifest.get('data_schema')!=1 or not isinstance(manifest.get('files'),dict):raise ValueError('إصدار بيانات غير مدعوم')
        if set(names)!=(set(manifest['files'])|{'APP-MANIFEST.json'}):raise ValueError('قائمة ملفات الحزمة غير مطابقة')
        for info in infos:
            if info.filename=='APP-MANIFEST.json':continue
            if (info.external_attr>>16)&0o170000==0o120000 or info.flag_bits&1:raise ValueError('رابط أو ملف مشفر في الحزمة')
            target=release_path(stage,info.filename);item=manifest['files'][info.filename]
            if item['bytes']!=info.file_size:raise ValueError('حجم ملف غير مطابق')
            target.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest)
            if digest(target)!=item['sha256']:raise ValueError('بصمة ملف غير مطابقة: '+info.filename)
        release=json.loads((stage/'release.json').read_text())
        if release['version']!=manifest['release'] or release['data_schema']!=1:raise ValueError('هوية إصدار غير متسقة')
        atomic_json(stage/'APP-MANIFEST.json',manifest)
        return manifest


def owned_services(root):
    import psutil
    root=Path(root).resolve();result=[]
    for service,name in [('studio','project_studio.py'),('manager','daily_ai.py')]:
        pidfile=root/'daily'/('projects.pid' if service=='studio' else 'manager.pid')
        try:
            p=psutil.Process(int(pidfile.read_text()));cmd=p.cmdline()
            if any(Path(a).resolve()==root/name for a in cmd if a.lower().endswith('.py')):result.append((service,p))
        except (OSError,ValueError,psutil.Error):pass
    return result


def stop_services(root):
    import urllib.request
    services=owned_services(root)
    if not services:return []
    def get(port,path):return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}'+path,timeout=5))
    if any(name=='studio' for name,p in services):
        state=get(18078,'/api/state')
        if state.get('importing') or state.get('running_pid') or (state.get('job') or {}).get('status')=='running' or (state.get('repair_run') or {}).get('status')=='running':raise ValueError('أوقف المهمة أو التطبيق مؤقتًا قبل الترقية')
    if any(name=='manager' for name,p in services):
        state=get(18079,'/api/state')
        if state.get('generating') or state.get('status') in {'loading','stopping'}:raise ValueError('النموذج مشغول؛ لم تتوقف الخدمات')
        request=urllib.request.Request('http://127.0.0.1:18079/api/client-session',headers={'X-Local-Session':'refresh'})
        token=json.load(urllib.request.urlopen(request))['token']
        request=urllib.request.Request('http://127.0.0.1:18079/api/stop',data=b'{}',headers={'X-Local-Token':token,'Content-Type':'application/json'})
        urllib.request.urlopen(request,timeout=10).close()
        for _ in range(100):
            state=get(18079,'/api/state')
            if state.get('status')=='stopped':break
            time.sleep(.1)
        else:raise ValueError('لم يتوقف النموذج؛ أُلغيت الترقية')
    for name,p in services:p.terminate();p.wait(15)
    return [name for name,p in services]


def require_offline(root):
    if owned_services(root):raise ValueError('الخدمات تعمل؛ استخدم Update-App.ps1 أو أوقفها قبل الصيانة')


def snapshot_data(root,backup):
    root=Path(root);targets=[];daily=root/'daily'
    targets += [(p,Path('daily')/p.name) for p in daily.glob('*.json') if p.is_file() and not p.is_symlink()]
    project=None
    try:project=Path(json.loads((daily/'projects-settings.json').read_text(encoding='utf-8'))['project']).resolve()
    except (OSError,KeyError,ValueError):pass
    if project and (project/'.local-ai').is_dir():
        meta=project/'.local-ai'
        if not meta.resolve().is_relative_to(project) or meta.is_symlink():raise ValueError('مسار بيانات المشروع غير صالح')
        from app_maintenance import ensure_schema
        ensure_schema(meta)
        targets += [(contained(meta,p.relative_to(meta).as_posix()),Path('project-meta')/p.relative_to(meta)) for p in meta.rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))]
    if sum(p.stat().st_size for p,rel in targets)>2*1024**3:raise ValueError('نسخة البيانات أكبر من 2GB؛ لم تبدأ الترقية')
    rows=[]
    for source,relative in targets:
        if source.is_symlink():raise ValueError('رابط داخل بيانات النسخة الاحتياطية')
        destination=contained(backup,relative.as_posix());destination.parent.mkdir(parents=True,exist_ok=True)
        if source.suffix in {'.sqlite3','.db'}:
            with closing(sqlite3.connect(source)) as a,closing(sqlite3.connect(destination)) as b:
                a.backup(b)
                b.commit()
        else:shutil.copy2(source,destination)
        rows.append({'path':relative.as_posix(),'sha256':digest(destination),'bytes':destination.stat().st_size})
    atomic_json(Path(backup)/'DATA-MANIFEST.json',{'project':str(project) if project else None,'files':rows,'created':time.time()})
    return rows


def replace_file(source,target):
    target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.update')
    with Path(source).open('rb') as a,temporary.open('xb') as b:shutil.copyfileobj(a,b);b.flush();os.fsync(b.fileno())
    for attempt in range(6):
        try:temporary.replace(target);return
        except PermissionError:
            if attempt==5:raise
            time.sleep(.03*(attempt+1))


def rollback(root,identifier=None):
    root=Path(root).resolve();require_offline(root)
    journal_path=root/'.update-journal.json'
    if identifier:
        if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('معرّف نسخة غير صالح')
        record=json.loads(contained(root,'.updates/backups/'+identifier+'/transaction.json').read_text(encoding='utf-8'))
    else:record=json.loads(journal_path.read_text(encoding='utf-8'))
    if len(record['id'])!=32 or any(c not in '0123456789abcdef' for c in record['id']):raise ValueError('Invalid recovery identifier')
    backup=contained(root,'.updates/backups/'+record['id'])
    # Reject post-upgrade source edits rather than overwriting them during recovery.
    for name,item in record['files'].items():
        target=release_path(root,name);current=digest(target) if target.exists() else None
        if current not in {item['old'],item['new']}:raise ValueError('تغير ملف بعد الترقية؛ راجع النسخة قبل الاسترجاع: '+name)
        if item['old'] and digest(contained(backup,'code/'+name))!=item['old']:raise ValueError('نسخة رجوع تالفة')
    for name,item in record['files'].items():
        target=release_path(root,name)
        if item['old']:replace_file(contained(backup,'code/'+name),target)
        elif target.exists():target.unlink()
    # Never roll back chats or drafts: data migration is additive and backward-compatible.
    record.update(status='rolled_back',finished=time.time());atomic_json(journal_path,record)
    return {'rolled_back':record['id'],'data_preserved':True}


def recover(root):
    path=Path(root)/'.update-journal.json'
    if not path.exists():return {'recovered':False}
    value=json.loads(path.read_text(encoding='utf-8'))
    if value['status'] in {'applying','verifying','failed'}:return rollback(root)
    return {'recovered':False,'status':value['status']}


def install(root,package,sha,stop=False,self_check=True,fail_after=None):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    if root==Path(root.anchor) or root.is_symlink():raise ValueError('مجلد تثبيت غير صالح')
    with exclusive(contained(root,'.release-update.lock')):
        recover(root)
        identifier=uuid.uuid4().hex;stage=contained(root,'.updates/staging/'+identifier);stage.mkdir(parents=True)
        manifest=extract(package,sha,stage)
        total=sum(i['bytes'] for i in manifest['files'].values())
        if shutil.disk_usage(root).free<total*3+64*1024*1024:raise OSError('المساحة لا تكفي للتجهيز والنسخة الاحتياطية')
        # Compile/resolve dependencies before stopping a working installation.
        import py_compile
        for name in manifest['files']:
            if name.endswith('.py') and '/' not in name:py_compile.compile(str(stage/name),doraise=True)
        from app_runtime import prepare
        prepared=prepare(stage,activate=False) if self_check else None
        if self_check:
            result=subprocess.run([prepared['python'],'-I',str(stage/'app_runtime.py'),'--root',str(stage),'self-test'],capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode:raise RuntimeError('فشل فحص الحزمة المعزول: '+result.stderr[-2000:])
        if stop:stopped=stop_services(root)
        else:require_offline(root);stopped=[]
        backup=contained(root,'.updates/backups/'+identifier);backup.mkdir(parents=True)
        snapshot_data(root,backup/'data')
        # Track the installed manifest too, so rollback restores the release identity.
        names={**manifest['files'],'APP-MANIFEST.json':{'sha256':digest(stage/'APP-MANIFEST.json')}}
        files={}
        for name,item in names.items():
            target=release_path(root,name);old=digest(target) if target.exists() else None
            files[name]={'old':old,'new':item['sha256']}
            if old:
                destination=contained(backup,'code/'+name);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,destination)
        record={'id':identifier,'status':'applying','release':manifest['release'],'created':time.time(),'files':files,'stopped':stopped}
        atomic_json(backup/'transaction.json',record);atomic_json(root/'.update-journal.json',record)
        try:
            for index,name in enumerate(files):
                replace_file(stage/name,release_path(root,name))
                if fail_after is not None and index+1>=fail_after:raise OSError('Injected update interruption')
            record['status']='verifying';atomic_json(root/'.update-journal.json',record)
            for name,item in files.items():
                if digest(release_path(root,name))!=item['new']:raise OSError('فشل التحقق من التثبيت')
            if self_check:
                from app_runtime import prepare as ensure
                installed=ensure(root)
                checked=subprocess.run([installed['python'],'-I',str(root/'app_runtime.py'),'--root',str(root),'self-test'],capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                if checked.returncode:raise RuntimeError('Installed self-test failed: '+checked.stderr[-2000:])
            record.update(status='complete',finished=time.time());atomic_json(root/'.update-journal.json',record)
            # Only this transaction's resolved staging directory is disposable.
            try:
                if stage.resolve().parent==(root/'.updates/staging').resolve() and not stage.is_symlink():shutil.rmtree(stage)
            except OSError:pass  # A locked staging file cannot invalidate a verified installation.
            return {'installed':manifest['release'],'backup_id':identifier,'files':len(files),'stopped':stopped,'data_preserved':True}
        except Exception:
            rollback(root);raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(Path(__file__).resolve().parent));sub=parser.add_subparsers(dest='command',required=True)
    update=sub.add_parser('install');update.add_argument('package');update.add_argument('--sha256',required=True);update.add_argument('--stop-services',action='store_true')
    back=sub.add_parser('rollback');back.add_argument('--id');sub.add_parser('recover');sub.add_parser('status');args=parser.parse_args()
    if args.command=='install':result=install(args.root,args.package,args.sha256,args.stop_services)
    elif args.command in {'rollback','recover'}:
        with exclusive(contained(args.root,'.release-update.lock')):
            result=rollback(args.root,args.id) if args.command=='rollback' else recover(args.root)
    else:
        path=Path(args.root)/'.update-journal.json';result=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status':'none'}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as error:print(str(error),file=sys.stderr);sys.exit(1)
