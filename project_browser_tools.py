"""Owned, disposable browser + diagnostic static preview; no existing browser access."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parent
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

def private(ws,name):
    p=ws.meta/name
    if not p.resolve().is_relative_to(ws.meta.resolve()):raise ValueError('مسار بيانات الأداة غير صالح')
    p.mkdir(parents=True,exist_ok=True)
    return p

class Browser:
    def __init__(self,ws):self.ws=ws;self.process=None;self.preview=None;self.log=None;self.output=queue.Queue(maxsize=4);self.state_key=uuid.uuid4().hex
    def close(self):
        for process in (self.process,self.preview):
            if process and process.poll() is None:
                try:
                    if process is self.process:process.stdin.close()
                    else:process.terminate()
                    process.wait(timeout=5)
                except (OSError,ValueError,subprocess.TimeoutExpired):
                    import psutil
                    try:
                        owner=psutil.Process(process.pid)
                        for child in owner.children(recursive=True):
                            try:child.kill()
                            except psutil.Error:pass
                        owner.kill();process.wait(timeout=5)
                    except psutil.Error:pass
        for process in (self.process,self.preview):
            if process:
                for stream in (process.stdin,process.stdout):
                    if stream:
                        try:stream.close()
                        except (OSError,ValueError):pass
        if self.log:self.log.close()
        self.process,self.preview,self.log=None,None,None
    def call(self,action,entry='index.html',selector='',value='',expected='',contains=False,width=1280,target='auto'):
        if not all(isinstance(v,str) for v in (action,entry,selector,value,expected)) or max(map(len,(entry,selector,value,expected)))>10000 or type(contains) is not bool or type(width) is not int:raise ValueError('وسائط المتصفح غير صالحة')
        if action not in {'open','snapshot','click','fill','select','press','assert','screenshot','viewport','reload','close'}:raise ValueError('عملية متصفح غير مدعومة')
        if not isinstance(target,str) or target not in {'auto','text','value','checked','visible','enabled','count','dialog_count'}:raise ValueError('نوع تأكيد المتصفح غير صالح')
        if action=='close':self.close();return {'closed':True}
        directory=private(self.ws,'tools/browser')
        command={'action':action,'selector':selector,'value':value,'expected':expected,'contains':contains,'width':width,'target':target}
        if action=='open':
            command['state_key']=self.state_key
            self.close();file=self.ws.path(entry)
            if not file.is_file() or file.suffix.lower() not in {'.html','.htm'}:raise ValueError('اختر صفحة HTML موجودة لفحصها؛ لا تُشغّل الأداة خوادم backend غير معروفة')
            self.log=(directory/'browser.log').open('ab')
            self.preview=subprocess.Popen([sys.executable,str(ROOT/'project_preview.py'),'--root',str(self.ws.root),'--entry',file.relative_to(self.ws.root).as_posix(),'--watch-parent',str(os.getpid())],stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf-8',creationflags=FLAGS)
            lines=queue.Queue()
            threading.Thread(target=lambda:lines.put(self.preview.stdout.readline()),daemon=True).start()
            try:line=lines.get(timeout=10)
            except queue.Empty:self.close();raise ValueError('تعذر بدء معاينة الفحص')
            if not line.startswith('PREVIEW_URL='):self.close();raise ValueError('لم تصل وصلة معاينة صحيحة')
            command['url']=line.strip().split('=',1)[1]
            env=os.environ.copy();env['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'runtime/browser')
            self.process=subprocess.Popen([str(ROOT/'runtime/node/node.exe'),str(ROOT/'project_browser_runner.js')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf-8',env=env,creationflags=FLAGS)
            self.output=queue.Queue(maxsize=4)
            process=self.process;output=self.output
            def read():
                try:
                    for line in process.stdout:output.put(line)
                finally:output.put(None)
            threading.Thread(target=read,daemon=True).start()
        if not self.process or self.process.poll() is not None:raise ValueError('انتهت جلسة المتصفح؛ افتح المعاينة مجددًا دون إعادة الأوامر المكتملة')
        if action=='screenshot':command.update(directory=str(directory),id=uuid.uuid4().hex)
        self.process.stdin.write(json.dumps(command,ensure_ascii=False)+'\n');self.process.stdin.flush()
        try:line=self.output.get(timeout=35)
        except queue.Empty:self.close();raise ValueError('انتهت مهلة المتصفح؛ لم تُعد الإجراءات تلقائيًا')
        if not line:self.close();raise ValueError('توقفت جلسة المتصفح')
        result=json.loads(line)
        if not result['ok']:raise ValueError(result['error'])
        return {**result['result'],'diagnostic_preview':True,'scope':'Owned static project preview, not a validation pass or delivery receipt.'}
