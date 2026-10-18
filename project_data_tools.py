"""Read-only Git/SQLite inspection. No commits, database writes, or shell strings."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time


def git_inspect(ws, action='status',path=''):
    if action not in {'status','diff','history'}:raise ValueError('عمليات Git المتاحة: status, diff, history')
    executable=shutil.which('git')
    if not executable:raise ValueError('Git غير مثبت على الجهاز')
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_TERMINAL_PROMPT='0',GIT_OPTIONAL_LOCKS='0')
    base=[executable,'--no-optional-locks','-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false','-c','core.pager=cat','-C',str(ws.root)]
    def run(args):
        r=subprocess.run(base+args,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if r.returncode:raise ValueError(r.stderr[:1500] or 'تعذر فحص Git')
        return r.stdout
    try:root=run(['rev-parse','--show-toplevel']).strip()
    except ValueError:return {'available':False,'reason':'المشروع ليس مستودع Git؛ لم تتم تهيئته أو تعديل ملفات Git'}
    if Path(root).resolve()!=ws.root:raise ValueError('المستودع ينتمي إلى مجلد أب؛ افتح جذر المشروع الصحيح')
    tail=['--',str(ws.path(path).relative_to(ws.root))] if path else ['--','.',' :(exclude).local-ai']
    if not path:tail=['--','.',' :(exclude).local-ai'.strip()]
    args={'status':['status','--short','--untracked-files=normal'],
          'diff':['diff','--no-ext-diff','--no-textconv','--no-color','HEAD'],
          'history':['log','-10','--format=%h %ad %s','--date=short']}[action]
    text=run(args+tail)
    return {'available':True,'action':action,'output':text[:16000],'truncated':len(text)>16000,'read_only':True}


def database_read(ws,path,query='',limit=50):
    file=ws.path(path)
    if file.suffix.lower() not in {'.db','.sqlite','.sqlite3'} or not file.is_file():raise ValueError('اختر قاعدة SQLite داخل المشروع')
    with file.open('rb') as stream:
        if stream.read(16)!=b'SQLite format 3\0':raise ValueError('هذا الملف ليس قاعدة SQLite صالحة')
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('حد الصفوف بين 1 و100')
    if not isinstance(query,str) or len(query)>8000:raise ValueError('استعلام غير صالح')
    sql=query.strip() or "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
    db=sqlite3.connect(file.as_uri()+'?mode=ro',uri=True,timeout=1)
    start=time.monotonic()
    try:
        db.enable_load_extension(False);db.execute('PRAGMA query_only=ON')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,1024*1024)
        db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH,100)
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
        def auth(op,a,b,*_):
            if op not in allowed:return sqlite3.SQLITE_DENY
            if op==sqlite3.SQLITE_FUNCTION and str(b or a).lower() in {'load_extension','writefile','readfile','fts3_tokenizer'}:return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(auth);db.set_progress_handler(lambda:int(time.monotonic()-start>3),1000)
        cursor=db.execute(sql);rows=cursor.fetchmany(limit+1)
        def safe(v):return {'blob_bytes':len(v)} if isinstance(v,bytes) else v[:1000] if isinstance(v,str) else v
        return {'columns':[d[0] for d in cursor.description or []],'rows':[[safe(v) for v in row] for row in rows[:limit]],'truncated':len(rows)>limit,'read_only':True,'seconds':round(time.monotonic()-start,3)}
    except sqlite3.Error as error:raise ValueError('فحص SQLite للقراءة فقط: '+str(error)) from error
    finally:db.close()
