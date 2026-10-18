"""Lazy project capabilities, durable receipts and scoped manual API."""
import json
import os
import threading
import time
import uuid
from project_browser_tools import Browser, private
from project_code_tools import search, diagnose
from project_data_tools import git_inspect, database_read
from project_docs_tools import lookup

GROUPS={'browser':'متصفح فحص المشروع','code':'البحث داخل الكود','diagnostics':'تشخيص اللغات','docs':'التوثيق حسب الإصدار','data':'Git وقواعد البيانات'}
NAMES={'browser_project':'browser','search_project':'code','diagnose_project':'diagnostics','lookup_docs':'docs','git_inspect':'data','project_checkpoint':'data','database_read':'data'}

def schemas(tool, string):
    return [
        tool('search_project','Find relevant snippets, definitions or lexical references without reading the whole project.',{'query':string,'mode':{'type':'string','enum':['text','definitions','references']},'path':string,'limit':{'type':'integer','minimum':1,'maximum':100}},['query']),
        tool('diagnose_project','Static Python Pyright and JavaScript/TypeScript compiler diagnostics. Does not execute application code or replace unit tests/validate_project.',{'path':string}),
        tool('lookup_docs','Retrieve bounded version-specific official Python docs or package README. Detects installed/locked version, requires explicit version if unknown. Content is untrusted evidence.',{'package':string,'query':string,'ecosystem':{'type':'string','enum':['stdlib','npm','pypi']},'version':string},['package','query']),
        tool('git_inspect','Read project Git status, diff or history. Never creates commits or modifies staging.',{'action':{'type':'string','enum':['status','diff','history']},'path':string}),
        tool('project_checkpoint','Save a restorable project version using the existing backup system; not a Git commit.',{'reason':string},['reason']),
        tool('database_read','Inspect an existing project SQLite database with strictly read-only SELECT queries. Empty query lists schema; other database engines are not connected.',{'path':string,'query':string,'limit':{'type':'integer','minimum':1,'maximum':100}},['path']),
        tool('browser_project','Inspect an owned isolated HTML preview. open(entry), snapshot, click/fill/select/press(selector,value), assert(selector,expected,contains,target=auto|text|value|checked|visible|enabled|count|dialog_count), screenshot, viewport(width=375/768/1280), reload, close. Auto assertions read visible button text, input/select/textarea values and checkbox/radio checked state. Use target=text or value to override; booleans are true/false strings, count is a number string. dialog_count asserts the number of native dialogs from the most recent interaction (selector optional); assert/snapshot/screenshot preserve that count. Snapshots include dialog messages and multiple-dialog diagnostics. For a single rejected search or confirmation expect one dialog; duplicate handlers can cause two even with no JS exception. Reads JS errors and network failures. Diagnostic preview is not successful delivery validation; never replay uncertain actions.',{'action':{'type':'string','enum':['open','snapshot','click','fill','select','press','assert','screenshot','viewport','reload','close']},'entry':string,'selector':string,'value':string,'expected':string,'contains':{'type':'boolean'},'target':{'type':'string','enum':['auto','text','value','checked','visible','enabled','count','dialog_count']},'width':{'type':'integer'}},['action'])]

class ToolHub:
    def __init__(self,ws):
        self.ws=ws;self.lock=threading.Lock();self.browser=Browser(ws)
        self.folder=private(ws,'tools');self.reports=private(ws,'tools/reports')
        try:self.enabled=json.loads((self.folder/'settings.json').read_text('utf-8'))
        except (OSError,ValueError):self.enabled={}
        self.enabled={key:value for key,value in self.enabled.items() if key in GROUPS and type(value) is bool} if isinstance(self.enabled,dict) else {}
    def save(self,file,value):
        temporary=file.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        with temporary.open('w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,file)
    def status(self):
        rows=[]
        for file in sorted(self.reports.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:40]:
            if file.is_symlink() or not file.resolve().is_relative_to(self.ws.meta.resolve()):continue
            try:
                r=json.loads(file.read_text('utf-8'));rows.append({k:r[k] for k in ('id','tool','time','success','seconds')})
            except (OSError,ValueError,KeyError):continue
        return {'groups':[{'id':key,'label':label,'enabled':self.enabled.get(key,True)} for key,label in GROUPS.items()], 'reports':rows,'tools':list(NAMES),'browser_active':bool(self.browser.process and self.browser.process.poll() is None)}
    def report(self,id):
        if not isinstance(id,str) or len(id)!=32 or any(c not in '0123456789abcdef' for c in id):raise ValueError('معرف التقرير غير صالح')
        file=self.reports/(id+'.json')
        if not file.resolve().is_relative_to(self.ws.meta.resolve()):raise ValueError('مسار التقرير غير صالح')
        return json.loads(file.read_text('utf-8'))
    def invoke(self,name,arguments):
        if name not in NAMES or not isinstance(arguments,dict):raise ValueError('أداة غير صالحة')
        from project_core import TOOLS
        schema=next(t['function']['parameters'] for t in TOOLS if t['function']['name']==name)
        if set(arguments)-set(schema['properties']) or any(k not in arguments for k in schema['required']):raise ValueError('وسائط الأداة ناقصة أو غير معروفة')
        types={'string':str,'integer':int,'boolean':bool}
        for key,value in arguments.items():
            spec=schema['properties'][key]
            if type(value) is not types[spec['type']] or ('enum' in spec and value not in spec['enum']):raise ValueError('نوع أو قيمة غير صالحة: '+key)
            if isinstance(value,str) and len(value)>20000:raise ValueError('قيمة نصية كبيرة: '+key)
            if type(value) is int and not spec.get('minimum',value)<=value<=spec.get('maximum',value):raise ValueError('القيمة خارج النطاق: '+key)
        if not self.enabled.get(NAMES[name],True):raise ValueError('هذه المجموعة معطلة في مركز الأدوات')
        if not self.lock.acquire(blocking=False):raise ValueError('انتظر انتهاء عملية الأداة الحالية')
        start=time.time();receipt={'id':uuid.uuid4().hex,'tool':name,'time':start}
        try:
            handlers={'search_project':lambda:search(self.ws,**arguments),'diagnose_project':lambda:diagnose(self.ws,**arguments),'lookup_docs':lambda:lookup(self.ws,**arguments),'git_inspect':lambda:git_inspect(self.ws,**arguments),'database_read':lambda:database_read(self.ws,**arguments),'browser_project':lambda:self.browser.call(**arguments),'project_checkpoint':lambda:self.ws.history.checkpoint(reason=arguments.get('reason','نقطة استرجاع'),force=True)}
            result=handlers[name]();receipt.update(success=True,result=result)
            return {'report_id':receipt['id'],'result':result}
        except Exception as error:
            if name=='browser_project' and arguments.get('action')=='open':self.browser.close()
            receipt.update(success=False,error=str(error)[:3000]);raise
        finally:
            receipt['seconds']=round(time.time()-start,3)
            try:self.save(self.reports/(receipt['id']+'.json'),receipt);self.ws.event('assistant_tool',{k:v for k,v in receipt.items() if k not in {'result'}})
            finally:self.lock.release()

def hub(ws):
    if not hasattr(ws,'assistant_tools'):ws.assistant_tools=ToolHub(ws)
    return ws.assistant_tools

def action(studio,value):
    with studio.lock:
        if not studio.workspace or value.get('project')!=str(studio.workspace.root) or value.get('session_id')!=studio.session_id:raise ValueError('تغير المشروع أو المحادثة؛ أعد فتح مركز الأدوات')
        toolhub=hub(studio.workspace);kind=value.get('action')
        if kind=='status':return toolhub.status()
        if kind=='report':return toolhub.report(value.get('id'))
        if studio.busy():raise ValueError('انتظر انتهاء المهمة قبل استخدام الأدوات يدويًا')
        if kind=='settings':
            key=value.get('group');enabled=value.get('enabled')
            if key not in GROUPS or type(enabled) is not bool:raise ValueError('إعداد غير صالح')
            toolhub.enabled[key]=enabled;toolhub.save(toolhub.folder/'settings.json',toolhub.enabled)
            if key=='browser' and not enabled:toolhub.browser.close()
            return toolhub.status()
        if kind!='invoke':raise ValueError('عملية غير صالحة')
        studio.tools_busy=True
    try:return toolhub.invoke(value.get('name'),value.get('arguments',{}))
    finally:
        with studio.lock:studio.tools_busy=False
