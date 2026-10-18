"""Versioned metadata, rotating diagnostics, and reversible retention of disposable exports."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager

SCHEMA = 1
DEFAULT_POLICY = {'version':1, 'log_mb':8, 'hot_archives':7, 'export_days':30}
_locks, _guard = {}, threading.Lock()


def process_identity(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,int(pid))
        if not handle:return None
        try:
            values=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*[ctypes.byref(v) for v in values]):return None
            # An exited process has an exit timestamp even while a handle is held.
            if values[1].dwLowDateTime or values[1].dwHighDateTime:return None
            return (values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
        finally:kernel.CloseHandle(handle)
    try:os.kill(int(pid),0);return int(pid)
    except OSError:return None


@contextmanager
def exclusive(path):
    path=Path(path)
    for _ in range(2):
        try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY);break
        except FileExistsError:
            old=json.loads(path.read_text(encoding='utf-8'))
            if process_identity(old['pid'])==old['identity']:raise RuntimeError('عملية صيانة أخرى جارية')
            path.unlink()
    else:raise RuntimeError('تعذر امتلاك قفل الصيانة')
    try:
        os.write(fd,json.dumps({'pid':os.getpid(),'identity':process_identity(os.getpid())}).encode());os.fsync(fd);os.close(fd)
        yield
    finally:path.unlink(missing_ok=True)


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
    for attempt in range(6):
        try: temporary.replace(path); return
        except PermissionError:
            if attempt == 5: raise
            time.sleep(.02*(attempt+1))


def contained(root, relative):
    root = Path(root).resolve(); name = str(relative).replace('\\','/')
    parts = PurePosixPath(name).parts
    reserved={'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
    if any(p.split('.')[0].upper() in reserved for p in parts):raise ValueError('اسم Windows محجوز')
    if not parts or name.startswith('/') or any(p in {'.','..'} or ':' in p or p.endswith((' ','.')) for p in parts):
        raise ValueError('مسار صيانة غير صالح')
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root) or any(p.is_symlink() for p in [target,*target.parents] if p != root and p.is_relative_to(root)):
        raise ValueError('روابط أو مسار خارج مجلد الصيانة')
    return target


def digest(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def ensure_schema(meta):
    meta = Path(meta); path = contained(meta, 'schema.json')
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != SCHEMA: raise ValueError('إصدار بيانات أحدث أو غير مدعوم؛ لم تُعدّل البيانات')
        return data
    # Additive migration: existing chat, history, drafts and SQLite files are untouched.
    data = {'version':SCHEMA, 'created':time.time(), 'migration':'additive_marker_only', 'backward_compatible':True}
    atomic_json(path,data); return data


def policy(meta, value=None):
    path = contained(meta,'retention-policy.json')
    result = json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(DEFAULT_POLICY)
    if value is not None:
        if not isinstance(value,dict) or set(value)-set(DEFAULT_POLICY):raise ValueError('حقول سياسة غير صالحة')
        result = {**DEFAULT_POLICY, **value}
    if (result.get('version') != 1 or any(type(result.get(k)) is not int for k in ('log_mb','hot_archives','export_days'))
            or not 1 <= result['log_mb'] <= 64 or not 1 <= result['hot_archives'] <= 100 or not 1 <= result['export_days'] <= 3650):
        raise ValueError('سياسة الاحتفاظ غير صالحة')
    if value is not None: atomic_json(path,result)
    return result


class RotatingLog:
    def __init__(self,path,max_bytes=8*1024*1024,hot=7):
        self.path=Path(path);self.max_bytes=max_bytes;self.hot=hot;self.closed=False
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with _guard:self.lock=_locks.setdefault(str(self.path.resolve()),threading.RLock())
    def write(self,raw):
        if isinstance(raw,str):raw=raw.encode('utf-8',errors='replace')
        with self.lock:
            if self.closed:return
            if self.path.is_symlink():raise ValueError('رابط في مسار السجل')
            if self.path.exists() and self.path.stat().st_size+len(raw)>self.max_bytes and self.path.stat().st_size:
                archive=contained(self.path.parent,'rotated');archive.mkdir(exist_ok=True)
                destination=archive/(self.path.name+'.'+str(time.time_ns())+'.'+uuid.uuid4().hex[:6]+'.gz')
                with self.path.open('rb') as source, destination.open('xb') as target:
                    with gzip.GzipFile(fileobj=target,mode='wb',compresslevel=1) as compressed:shutil.copyfileobj(source,compressed)
                    target.flush();os.fsync(target.fileno())
                # Verify before truncation; no diagnostic data is discarded.
                with gzip.open(destination,'rb') as check:self_hash=hashlib.file_digest(check,'sha256').hexdigest()
                if self_hash!=digest(self.path):raise OSError('فشل التحقق من أرشيف السجل')
                self.path.write_bytes(b'')
                archives=sorted(archive.glob(self.path.name+'.*.gz'),key=lambda p:p.name,reverse=True)
                cold=contained(self.path.parent,'cold');cold.mkdir(exist_ok=True)
                for old in archives[self.hot:]:old.replace(contained(cold,old.name))
            with self.path.open('ab') as f:f.write(raw)
    def flush(self):pass
    def close(self):self.closed=True


class Console(io.TextIOBase):
    def __init__(self,sink):self.sink=sink
    @property
    def encoding(self):return 'utf-8'
    def write(self,text):self.sink.write(text);return len(text)
    def flush(self):self.sink.flush()


def console_logging(root,service):
    sink=RotatingLog(Path(root)/'daily/logs'/(service+'-console.log'))
    sys.stdout=Console(sink);sys.stderr=Console(sink)


def append_activity(meta,event):
    settings=policy(meta)
    RotatingLog(contained(meta,'activity.jsonl'),settings['log_mb']*1024*1024,settings['hot_archives']).write(json.dumps(event,ensure_ascii=False)+'\n')


def inventory(meta):
    meta=Path(meta);groups={}
    for p in meta.rglob('*'):
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(meta.resolve()):continue
        group=p.relative_to(meta).parts[0];item=groups.setdefault(group,{'files':0,'bytes':0});item['files']+=1;item['bytes']+=p.stat().st_size
    return groups


def plan(meta):
    meta=Path(meta);settings=policy(meta);files=[];cutoff=time.time()-settings['export_days']*86400
    directory=contained(meta,'exports')
    if directory.exists():
        pairs={}
        for p in directory.iterdir():
            if p.suffix not in {'.zip','.json'} or len(p.stem)!=32 or any(c not in '0123456789abcdef' for c in p.stem):continue
            if p.is_symlink() or not p.is_file():continue
            pairs.setdefault(p.stem,[]).append(p)
        for group in pairs.values():
            if max(p.stat().st_mtime for p in group)>=cutoff:continue
            for p in group:
                relative=p.relative_to(meta).as_posix();contained(meta,relative)
                files.append({'path':relative,'bytes':p.stat().st_size,'sha256':digest(p),'mtime_ns':p.stat().st_mtime_ns})
    value={'id':uuid.uuid4().hex,'created':time.time(),'meta':str(meta.resolve()),'files':files,'bytes':sum(f['bytes'] for f in files),
           'policy':settings,'protected':['sessions','drafts','attachments','history','recovery','repair-runs','repair-attempts','requirements']}
    atomic_json(contained(meta,'retention/plans/'+value['id']+'.json'),value);return value


def archive(meta,plan_id):
    meta=Path(meta)
    if not isinstance(plan_id,str) or len(plan_id)!=32 or any(c not in '0123456789abcdef' for c in plan_id):raise ValueError('معرّف خطة غير صالح')
    value=json.loads(contained(meta,'retention/plans/'+plan_id+'.json').read_text(encoding='utf-8'))
    if value['meta']!=str(meta.resolve()) or time.time()-value['created']>600 or value['policy']!=policy(meta):raise ValueError('تغيرت السياسة أو انتهت صلاحية الخطة؛ أعد المعاينة')
    if not value['files']:return {'archived':0,'bytes':0}
    for item in value['files']:
        p=contained(meta,item['path'])
        if not item['path'].startswith('exports/') or p.stat().st_mtime_ns!=item['mtime_ns'] or digest(p)!=item['sha256']:raise ValueError('تغير ملف منذ المعاينة؛ لم تبدأ الأرشفة')
    if shutil.disk_usage(meta).free<value['bytes']+1024*1024:raise OSError('المساحة لا تكفي لإنشاء أرشيف قابل للاستعادة')
    destination=contained(meta,'retention/archives/'+plan_id+'.zip');destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise ValueError('طُبقت الخطة بالفعل')
    temporary=destination.with_suffix('.tmp')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        z.writestr('MANIFEST.json',json.dumps(value,ensure_ascii=False))
        for item in value['files']:z.write(contained(meta,item['path']),item['path'])
    with temporary.open('r+b') as f:os.fsync(f.fileno())
    with zipfile.ZipFile(temporary) as z:
        for item in value['files']:
            if hashlib.sha256(z.read(item['path'])).hexdigest()!=item['sha256']:raise OSError('أرشيف غير مطابق؛ لم يُحذف أي أصل')
    temporary.replace(destination)
    atomic_json(destination.with_suffix('.json'),{**value,'status':'committed','archive_sha256':digest(destination)})
    # All originals are now recoverable even if interruption occurs between unlinks.
    for item in value['files']:
        p=contained(meta,item['path'])
        if digest(p)!=item['sha256']:raise ValueError('تغير الملف بعد إنشاء الأرشيف؛ بقيت النسختان')
        p.unlink()
    return {'id':plan_id,'archived':len(value['files']),'bytes':value['bytes']}


def restore(meta,archive_id):
    meta=Path(meta)
    if not isinstance(archive_id,str) or len(archive_id)!=32 or any(c not in '0123456789abcdef' for c in archive_id):raise ValueError('معرّف أرشيف غير صالح')
    path=contained(meta,'retention/archives/'+archive_id+'.zip');record=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    if record['meta']!=str(meta.resolve()) or digest(path)!=record['archive_sha256']:raise ValueError('الأرشيف تالف أو لا يخص هذا المشروع')
    if shutil.disk_usage(meta).free<sum(f['bytes'] for f in record['files'])+1024*1024:raise OSError('المساحة لا تكفي للاستعادة')
    with zipfile.ZipFile(path) as z:
        # Preflight everything; never overwrite a newer file.
        for item in record['files']:
            target=contained(meta,item['path'])
            if not item['path'].startswith('exports/') or hashlib.sha256(z.read(item['path'])).hexdigest()!=item['sha256']:raise ValueError('ملف أرشيف غير صالح')
            if target.exists() and digest(target)!=item['sha256']:raise ValueError('يوجد ملف أحدث؛ لم تبدأ الاستعادة')
        for item in record['files']:
            target=contained(meta,item['path']);target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():
                temp=target.with_suffix('.restore-'+uuid.uuid4().hex+'.tmp')
                with temp.open('xb') as f:f.write(z.read(item['path']));f.flush();os.fsync(f.fileno())
                temp.replace(target)
    return {'restored':len(record['files']),'id':archive_id}
