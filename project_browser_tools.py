"""Owned, disposable browser + diagnostic static preview; no existing browser access."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid
from urllib.parse import urlsplit, unquote, quote

ROOT=Path(__file__).resolve().parent
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0

def private(ws,name):
    p=ws.meta/name
    if not p.resolve().is_relative_to(ws.meta.resolve()):raise ValueError('مسار بيانات الأداة غير صالح')
    p.mkdir(parents=True,exist_ok=True)
    return p

def preview_guidance(detail):
    """Explain static-preview API failures without changing captured evidence."""
    network = detail.get('network', [])
    if not isinstance(network, list):
        return {}
    missing = []
    for row in network:
        if not isinstance(row, dict) or row.get('status') != 404:
            continue
        url = row.get('url')
        if not isinstance(url, str):
            continue
        try:
            path = urlsplit(url).path
        except ValueError:
            continue
        if (path == '/api' or path.startswith('/api/')) and path not in missing:
            missing.append(path)
    if not missing:
        return {}
    return {'preview_recovery': {
        'code': 'static_preview_api_unavailable',
        'missing_api_paths': missing[:12],
        'instruction': 'This owned preview serves static files; it does not run the project backend. '
        'These API 404 responses do not establish a production backend defect. '
        'Do not change production API behavior merely to satisfy this preview or repeat the same failing interaction. '
        'For isolated DOM checks, use an explicitly labelled test-only fixture with deterministic API stubs installed before application scripts. '
        'Keep real backend integration tests separate and preserve their assertions. '
        'A stubbed browser assertion proves only fixture UI behavior, not live backend persistence. '
        'After source changes, validate_project and reopen the fixture before collecting new browser assertions. '
        'Use link_acceptance_evidence for existing criteria; do not mark missing integration evidence as passed.'}}

def preview_fingerprint(workspace):
    # Standalone read-only browser adapters may not expose source revisions.
    # Without a revision, never treat a previous page as current for reuse.
    fingerprint=getattr(workspace,'fingerprint',None)
    return fingerprint(force=True) if callable(fingerprint) else None


_MISSING_EXPECTED = object()


class Browser:
    def __init__(self,ws):self.ws=ws;self.process=None;self.preview=None;self.log=None;self.output=queue.Queue(maxsize=4);self.state_key=uuid.uuid4().hex;self.owned_children=[];self.current_entry=None;self.loaded_fingerprint=None;self.preview_origin=None
    def close(self):
        import psutil
        children=list(self.owned_children)
        for process in (self.process,self.preview):
            if process:
                try:children.extend(psutil.Process(process.pid).children(recursive=True))
                except psutil.Error:pass
        for process in (self.process,self.preview):
            if process and process.poll() is None:
                try:
                    if process is self.process:
                        if process.stdin is not None:process.stdin.close()
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
        # Windows venv launchers may exit before their Python server child.
        # Reap only descendants captured while their owned parent was alive.
        _,alive=psutil.wait_procs(list({(p.pid,p.create_time()):p for p in children}.values()),timeout=2)
        for child in alive:
            try:child.kill()
            except psutil.Error:pass
        _,alive=psutil.wait_procs(alive,timeout=5)
        if alive:raise RuntimeError('Owned browser/preview descendants did not stop; cleanup is incomplete')
        for process in (self.process,self.preview):
            if process:
                for stream in (process.stdin,process.stdout):
                    if stream:
                        try:stream.close()
                        except (OSError,ValueError):pass
        if self.log:self.log.close()
        self.process,self.preview,self.log=None,None,None
        self.owned_children=[]
        self.current_entry=None;self.loaded_fingerprint=None;self.preview_origin=None
    def call(self,action,entry=None,selector='',value='',expected=_MISSING_EXPECTED,contains=False,width=1280,target='auto'):
        missing_expected = expected is _MISSING_EXPECTED
        if missing_expected:
            expected = ''
        explicit_entry = entry is not None
        entry = 'index.html' if entry is None else entry
        if (action=='reload' and getattr(self,'preview_mode','static')=='backend'
                and self.loaded_fingerprint != preview_fingerprint(self.ws)):
            # Reloading HTML cannot reload already imported Python backend code.
            # Prepare a fresh owned backend/data session; never certify old code
            # as the new fingerprint or replay previous mutations.
            prepared_entry=getattr(self,'backend_entry',None) or entry
            self.close()
            prepared=self.call('open',entry=prepared_entry,width=width)
            return {**prepared,'session_recovered':True,'backend_restarted':True,'not_executed':True,
                'instruction':'Sources changed: a fresh backend and disposable database were prepared. No prior clicks, submissions or assertions were replayed. Recreate the scenario explicitly before collecting new proof.'}
        if not all(isinstance(v,str) for v in (action,entry,selector,value,expected)) or max(map(len,(entry,selector,value,expected)))>10000 or type(contains) is not bool or type(width) is not int:raise ValueError('وسائط المتصفح غير صالحة')
        if action not in {'open','snapshot','click','fill','select','press','upload','assert','screenshot','viewport','reload','close'}:raise ValueError('عملية متصفح غير مدعومة')
        if not isinstance(target,str) or target not in {'auto','text','value','checked','visible','enabled','count','dialog_count','image_format'}:raise ValueError('نوع تأكيد المتصفح غير صالح')
        if action=='assert' and target in {'count','dialog_count'} and (not expected.isascii() or not expected.isdecimal()):
            raise ValueError('Invalid browser assertion: count/dialog_count expected must be a non-negative integer STRING such as \"1\", never exists. Use target=visible, expected=\"true\" for visibility. Choose the expected outcome from the requirement; do not copy the observed result to force a pass.')
        if action=='assert' and target in {'visible','enabled','checked'} and expected not in {'true','false'}:
            raise ValueError('Invalid browser assertion: boolean targets require expected=\"true\" or \"false\". Preserve the required outcome.')
        if action=='assert' and missing_expected:
            raise ValueError('Invalid browser assertion: expected comparison value is required. Supply it explicitly from the criterion; image_format uses expected="image/webp". Empty text/value is allowed only as an explicit empty string. No assertion was executed and no proof was produced.')
        if action=='close':self.close();return {'closed':True}
        opened_entry = opened_fingerprint = None
        navigate_existing = False
        if action == 'open':
            file = self.ws.path(entry)
            if not file.is_file() or file.suffix.lower() not in {'.html','.htm'}:
                raise ValueError('Choose an existing project HTML entrypoint')
            opened_entry = file.relative_to(self.ws.root).as_posix()
            self.backend_entry=opened_entry
            opened_fingerprint = preview_fingerprint(self.ws)
            if (opened_fingerprint is not None and self.process and self.process.poll() is None and self.preview and self.preview.poll() is None
                    and self.current_entry == opened_entry and self.loaded_fingerprint == opened_fingerprint):
                return {**self.call('snapshot'), 'preview_reused':True, 'navigation_not_replayed':True,
                    'next_action':'This exact page and source are already open. No new acceptance proof was created. Perform the current criterion scenario, then assert its expected result explicitly. Use reload only for a deliberate reset, or open a different HTML entrypoint. Do not repeat open to obtain evidence.'}

        if action not in {'open','close'} and explicit_entry and (not self.process or self.process.poll() is not None):
            prepared = self.call('open', entry=entry, width=width)
            return {**prepared, 'not_executed':True, 'session_recovered':True,
                    'requested_action_not_executed':action,
                    'instruction':'Only the explicitly selected static page was reopened. The requested assertion/click/fill/submission was NOT executed or replayed. Inspect this fresh snapshot, then choose the next operation explicitly. No acceptance proof was created.'}
        directory=private(self.ws,'tools/browser')
        command={'action':action,'selector':selector,'value':value,'expected':expected,'contains':contains,'width':width,'target':target}
        if action=='upload':command['upload']=upload_source(self.ws,value)
        if (action=='open' and self.process and self.process.poll() is None and self.preview
                and self.preview.poll() is None and self.preview_origin):
            navigate_existing=True
            command.update(url=self.preview_origin+'/'+quote(opened_entry,safe='/'),reuse_context=True)
        if action=='open' and not navigate_existing:
            command['state_key']=self.state_key
            self.close();file=self.ws.path(entry)
            if not file.is_file() or file.suffix.lower() not in {'.html','.htm'}:raise ValueError('اختر صفحة HTML موجودة لفحصها؛ لا تُشغّل الأداة خوادم backend غير معروفة')
            self.log=(directory/'browser.log').open('ab')
            backend_preview=(self.ws.root/'project-preview.json').exists()
            if backend_preview:
                from project_dynamic_preview import configuration
                configuration(self.ws.root)
            self.preview_mode='backend' if backend_preview else 'static'
            preview_script='project_dynamic_preview.py' if backend_preview else 'project_preview.py'
            self.preview=subprocess.Popen([sys.executable,str(ROOT/preview_script),'--root',str(self.ws.root),'--entry',file.relative_to(self.ws.root).as_posix(),'--watch-parent',str(os.getpid())],stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf-8',creationflags=FLAGS)
            lines=queue.Queue()
            preview_output = self.preview.stdout
            if preview_output is None:
                self.close()
                raise RuntimeError("Preview output pipe was not created")
            threading.Thread(target=lambda:lines.put(preview_output.readline()),daemon=True).start()
            try:line=lines.get(timeout=10)
            except queue.Empty:self.close();raise ValueError('تعذر بدء معاينة الفحص')
            if not line.startswith('PREVIEW_URL='):self.close();raise ValueError('لم تصل وصلة معاينة صحيحة')
            import psutil
            try:self.owned_children=psutil.Process(self.preview.pid).children(recursive=True)
            except psutil.Error:self.close();raise ValueError('Owned preview exited before its descendants could be tracked')
            command['url']=line.strip().split('=',1)[1]
            location=urlsplit(command['url']);self.preview_origin=location.scheme+'://'+location.netloc
            env=os.environ.copy();env['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'runtime/browser')
            self.process=subprocess.Popen([str(ROOT/'runtime/node/node.exe'),str(ROOT/'project_browser_runner.js')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf-8',env=env,creationflags=FLAGS)
            self.output=queue.Queue(maxsize=4)
            process=self.process;output=self.output
            browser_output = process.stdout
            if browser_output is None:
                self.close()
                raise RuntimeError("Browser output pipe was not created")
            def read():
                try:
                    for line in browser_output:output.put(line)
                finally:output.put(None)
            threading.Thread(target=read,daemon=True).start()
        if not self.process or self.process.poll() is not None:raise ValueError('انتهت جلسة المتصفح؛ افتح المعاينة مجددًا دون إعادة الأوامر المكتملة')
        if action=='screenshot':command.update(directory=str(directory),id=uuid.uuid4().hex)
        browser_input = self.process.stdin
        if browser_input is None:
            self.close()
            raise RuntimeError('Browser input pipe was not created')
        browser_input.write(json.dumps(command,ensure_ascii=False)+'\n');browser_input.flush()
        try:line=self.output.get(timeout=35)
        except queue.Empty:self.close();raise ValueError('انتهت مهلة المتصفح؛ لم تُعد الإجراءات تلقائيًا')
        if not line:self.close();raise ValueError('توقفت جلسة المتصفح')
        result=json.loads(line)
        if not result['ok']:
            error=browser_failure_guidance(result['error'],action,target)
            if 'waiting for locator(' in str(result['error']) and action!='snapshot':
                try:
                    current=self.call('snapshot')
                    catalog=(current.get('selector_catalog') or {}).get('entries',[])[:16]
                    pages=current.get('project_pages') or {}
                    error += '\nActual current page: '+str(current.get('url'))+'; selector_catalog='+json.dumps(catalog,ensure_ascii=False)+'; other_declared_pages='+json.dumps(pages,ensure_ascii=False)[:1200]+'. Copy an actual selector on the correct page; IDs are case-sensitive. File inputs require upload. Do not reopen the same page or patch application code merely to guess a selector. The failed action was not replayed.'
                except Exception:
                    pass
            raise ValueError(error)

        if action == 'open':
            self.current_entry=opened_entry;self.loaded_fingerprint=opened_fingerprint
        elif action == 'reload':
            self.loaded_fingerprint=preview_fingerprint(self.ws)
        # Clicks may navigate to another owned page. Cache the actual location,
        # so opening the previous entry is still a real page switch.
        location=urlsplit(str(result['result'].get('url') or ''))
        if location.path:
            self.current_entry=unquote(location.path).lstrip('/') + ('?'+location.query if location.query else '') + ('#'+location.fragment if location.fragment else '')
        backend_preview=getattr(self,'preview_mode','static')=='backend'
        payload={**result['result'], **({} if backend_preview else preview_guidance(result['result'])), **assertion_guidance(result['result']), 'diagnostic_preview':True,
            'preview_mode':'backend' if backend_preview else 'static',
            'scope':'Owned backend preview with disposable data, not production data or a delivery receipt.' if backend_preview else 'Owned static project preview, not a validation pass or delivery receipt.'}
        if action in {'open','snapshot'}:
            payload['project_pages']=declared_page_controls(self.ws,self.current_entry)
        return payload


def evidence_context(detail):
    """Actual browser page and viewport, excluding the transient preview port."""
    from urllib.parse import urlsplit
    parsed = urlsplit(str(detail.get('url') or ''))
    return {'page': parsed.path + ('?' + parsed.query if parsed.query else ''),
            'viewport': detail.get('viewport')}


def evidence_identity(arguments, detail):
    import hashlib
    payload = {'assertion': arguments, 'browser_context': evidence_context(detail)}
    interaction = (detail.get('assertion') or {}).get('interaction')
    if isinstance(interaction, dict):
        # A post-save assertion and a later reload/navigation assertion are
        # complementary observations, even for the same element/expected value.
        # Do not overwrite the actual mutation receipt with a read-only receipt.
        payload['interaction'] = {key:interaction.get(key) for key in
            ('action','selector','state_changed','mutation_observed')}
    return 'browser:' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def assertion_guidance(detail):
    """Keep failed evidence intact and return a specific recovery action."""
    row = detail.get('assertion') or {}
    if row.get('passed') is not False:
        return {}
    return {'assertion_recovery': {
        'code': 'browser_assertion_failed', 'target': row.get('target'),
        'expected': row.get('expected'), 'actual': row.get('actual'),
        'next_tool': 'browser_project',
        'instruction': 'This assertion failed and cannot be linked as passing proof. '
        'count/dialog_count require a numeric string; booleans require true/false strings. '
        'Text equality is exact; contains=true is only for an intentional requirement substring. '
        'Check the real expected behavior and selector, then correct the assertion or implement the missing behavior. '
        'Never copy actual into expected solely to pass. Do not search project files or npm docs for browser: evidence IDs; query link_acceptance_evidence after a successful fresh assertion.'}}


def assertion_recovery_pending(messages):
    """Unrelated inventory must not displace the most recent failed assertion."""
    for message in reversed(messages):
        if message.get('role') != 'tool':
            continue
        call_id = message.get('tool_call_id')
        call = next((call for parent in reversed(messages) for call in parent.get('tool_calls', []) if call.get('id') == call_id), None)
        if not call:
            continue
        name = call.get('function', {}).get('name')
        if name in {'write_file','patch_file','replace_file','delete_file'}:
            return False
        if name != 'browser_project':
            continue
        try:
            value = json.loads(message.get('content') or '{}')
        except (ValueError, TypeError):
            return False
        detail = value.get('result') or value
        return bool((detail.get('assertion') or {}).get('passed') is False or 'Invalid browser assertion:' in str(value.get('error') or ''))
    return False


def upload_source(ws, value):
    """Provide an explicit test input, never a fabricated conversion output."""
    import base64, re, io
    if value == '@test:png':
        value = 'data:image/png;base64,'+base64.b64encode(verified_test_png()).decode('ascii')
    match = re.fullmatch(r'data:image/png;base64,([A-Za-z0-9+/]+={0,2})', value)
    if match:
        try: data = base64.b64decode(match.group(1), validate=True)
        except ValueError: raise ValueError('Invalid PNG input encoding') from None
        if not data.startswith(b'\x89PNG\r\n\x1a\n') or not 8 < len(data) <= 7000:
            raise ValueError('PNG test INPUT must have a PNG signature and be at most 7000 bytes')
        try:
            from PIL import Image
            image=Image.open(io.BytesIO(data))
            if image.format!='PNG' or image.width*image.height>1024*1024 or min(image.size)<1:raise ValueError('PNG test input dimensions exceed the limit')
            image.verify()
            image=Image.open(io.BytesIO(data));image.load()
        except Exception as error:
            raise ValueError('Rejected PNG test INPUT: it is not a fully decodable valid PNG. Use value=@test:png for a verified synthetic INPUT; do not repeat the corrupted base64 or count this as a website conversion failure.') from error
        return {'name':'studio-test-input.png','mime':'image/png','base64':match.group(1),'input_verified':True,'input_dimensions':list(image.size),'scope':'verified synthetic test INPUT only; not conversion output'}
    match = re.fullmatch(r'data:text/csv;base64,([A-Za-z0-9+/]+={0,2})', value)
    if match:
        try:
            data = base64.b64decode(match.group(1), validate=True)
            if not 0 < len(data) <= 7000 or b'\0' in data:
                raise ValueError('CSV test input must be nonempty UTF-8 text without NUL bytes, at most 7000 bytes')
            data.decode('utf-8-sig')
        except (ValueError, UnicodeError) as error:
            raise ValueError('Invalid CSV INPUT: '+str(error)) from None
        return {'name':'studio-test-input.csv','mime':'text/csv','base64':match.group(1),
                'input_verified':True,'scope':'UTF-8 CSV test INPUT only; not import, persistence or acceptance evidence. Choose headers/data from the actual application contract and assert its actual resulting state.'}
    if value.startswith('@test:'):
        raise ValueError('Unknown test preset. Only @test:png exists. For CSV use an existing project .csv file or data:text/csv;base64 with UTF-8 content and the actual application headers. Do not click a file input to simulate upload. Input selection alone is not successful import or persistence.')
    if value.startswith('data:text/csv'):
        raise ValueError('CSV INPUT must use bounded data:text/csv;base64 with valid UTF-8 content')
    path = ws.path(value).resolve()
    if not path.is_relative_to(ws.root.resolve()) or path.is_relative_to(ws.meta.resolve()) or not path.is_file() or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif','.csv'} or path.stat().st_size > 8*1024*1024:
        raise ValueError('upload requires an existing image or UTF-8 .csv inside the project (not .local-ai), at most 8 MB, or @test:png for a verified synthetic PNG INPUT, or a bounded valid data:image/png;base64 input. For CSV an in-memory data:text/csv;base64 UTF-8 INPUT up to 7000 bytes is also allowed; choose actual application headers and verify actual imported state. Clicking with a filename does not select a file. Never supply a prebuilt converter output as acceptance evidence.')
    if path.suffix.lower() == '.csv':
        data=path.read_bytes()
        try:data.decode('utf-8-sig')
        except UnicodeError:raise ValueError('Project CSV input must be UTF-8') from None
        if b'\0' in data:raise ValueError('Project CSV input must be text without NUL bytes')
    return {'path':str(path)}


def browser_failure_guidance(error, action, target):
    if action != 'assert' or target != 'image_format':
        return error
    return ('WebP output assertion failed; NO conversion proof was produced. Diagnostic: '+str(error)+
            ' Next step: inspect the actual page and its conversion script to distinguish a wrong selector from missing implementation. '
            'If the actual converter output IMG is absent, implement the real conversion/output display, validate changed source, upload a PNG test INPUT, and assert image_format on that actual output. '
            'Do not repeat the missing selector on unchanged source, click the file input to simulate upload, or insert a prebuilt WebP output fixture. '
            'Use read_file and a small patch_file for the implementation when needed; preserve every criterion and valid existing link.')


def verified_test_png():
    """Canonical 1x1 PNG input with computed chunk checksums, never WebP output."""
    import struct,zlib
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00'))+chunk(b'IEND',b''))


def missing_expectation_tools(tools, messages):
    """Require an omitted comparison for one typed assertion recovery; never infer it."""
    calls = {call.get('id'):call.get('function',{}) for parent in messages for call in parent.get('tool_calls',[])}
    target = None
    for message in reversed(messages):
        if message.get('role') != 'tool':
            continue
        info = calls.get(message.get('tool_call_id')) or {}
        if info.get('name') in {'write_file','patch_file','replace_file','delete_file'}:
            break
        if info.get('name') != 'browser_project':
            continue
        try:
            arguments = json.loads(info.get('arguments') or '{}')
            result = json.loads(message.get('content') or '{}')
        except (ValueError,TypeError):
            break
        if not isinstance(arguments,dict) or not isinstance(result,dict):
            break
        detail = result.get('result') or result
        assertion = detail.get('assertion') or {}
        error = str(result.get('error') or '')
        omitted = 'expected' not in arguments
        bad_empty_boolean = (arguments.get('target') in {'visible','enabled','checked'}
                             and arguments.get('expected') in (None,'') and 'Invalid browser assertion: boolean targets require' in error)
        if (arguments.get('action') == 'assert' and (omitted or bad_empty_boolean)
                and ('Invalid browser assertion:' in error or assertion.get('passed') is False)):
            target = arguments.get('target','auto')
        break
    if not target:
        return tools
    import copy
    result = copy.deepcopy(tools)
    for entry in result:
        if entry['function']['name'] != 'browser_project':
            continue
        schema = entry['function']['parameters'];fields = schema['properties']
        fields['action']['enum'] = ['assert'];fields['target']['enum'] = [target];fields['expected'] = dict(fields['expected'])
        if target in {'visible','enabled','checked'}:
            fields['expected']['enum'] = ['true','false']
        elif target == 'image_format':
            fields['expected']['enum'] = ['image/webp']
        elif target in {'count','dialog_count'}:
            fields['expected']['pattern'] = '^[0-9]+$'
        required = ['action','target','expected'] + ([] if target == 'dialog_count' else ['selector'])
        schema['required'] = list(dict.fromkeys(schema.get('required',[])+required))
        entry['function']['description'] += ' Recover the last assertion with omitted expected: supply the explicit comparison STRING required by the original criterion. Boolean comparisons use true/false, counts use a non-negative integer string, and WebP format uses image/webp. Never copy the observed result merely to pass. This schema supplies no default outcome, executes nothing and grants no evidence. Successful assertion, source edit or another browser result ends this temporary syntax recovery.'
    return result


# Compatibility for callers of the earlier boolean-only recovery helper.
missing_boolean_expectation_tools = missing_expectation_tools


def navigation_tools(tools, browser, workspace):
    """Route by live host state, never by historical conversation snapshots."""
    import copy
    pages=[row['path'] for row in workspace.files() if Path(row['path']).suffix.lower() in {'.html','.htm'}]
    if not pages:return tools
    live=bool(browser and browser.process and browser.process.poll() is None
              and browser.preview and browser.preview.poll() is None)
    current=preview_fingerprint(workspace)
    if live and (browser.loaded_fingerprint is None or current is None):return tools
    state='missing' if not live else 'stale' if browser.loaded_fingerprint!=current else 'ready'
    result=copy.deepcopy(tools)
    for entry in result:
        function=entry['function']
        if function['name']!='browser_project':continue
        schema=function['parameters'];actions=schema['properties']['action'].get('enum',[])
        if state=='ready' and 'open' not in actions:continue
        if state=='ready' and len(pages)>128:continue
        def branch(allowed, entries=None):
            option=copy.deepcopy(schema)
            option['properties']['action']['enum']=allowed
            option['required']=['action']
            if entries is not None:
                option['properties']['entry']={'type':'string',**({'enum':entries} if len(entries)<=128 else {'minLength':1})}
                option['required'].append('entry')
            return option
        if state=='missing':
            branches=[branch(['snapshot'],pages)]
            title='Browser preview missing'
            note=' No live owned browser session exists. Historical successful opens do not establish readiness. Use snapshot with an explicit listed HTML entry to prepare the preview only; no assertion, interaction or previous submission will be replayed. Inspect the fresh result, then continue the criterion explicitly.'
        elif state=='stale':
            branches=[branch(['reload']),branch(['open'],pages)]
            title='Browser preview source changed'
            note=' The loaded page predates current source. Use explicit reload or open a listed entry to prepare current source; no interaction or assertion will be replayed. Then perform the preserved scenario and assert its required result.'
        else:
            others=[path for path in pages if path!=browser.current_entry]
            branches=[branch([a for a in actions if a not in {'open','snapshot'}])]
            if others:branches.append(branch(['open'],others))
            title='Browser preview ready'
            note=' The current page '+browser.current_entry+' is already open on current source and its latest DOM/selector_catalog was returned. Do not request an identical snapshot. Continue its scenario and assert actual state. open is available only for another listed HTML entrypoint; reload explicitly resets the current page. Navigation and snapshots do not prove acceptance.'
        schema['properties']['action']['enum']=list(dict.fromkeys(action for option in branches for action in option['properties']['action']['enum']))
        schema['required']=['action']
        schema['anyOf']=branches;schema['title']=title
        function['description']+=note
    return result


def enforce_navigation_call(choice, tools):
    from project_generation import ActionFormatError,validate_action_value
    for call in (choice.get('message') or {}).get('tool_calls') or []:
        function=call.get('function') or {}
        if function.get('name')!='browser_project':continue
        schema=next((entry['function']['parameters'] for entry in tools if entry['function']['name']=='browser_project'),None)
        if not schema or 'anyOf' not in schema:continue
        try:arguments=json.loads(function.get('arguments') or '{}')
        except (ValueError,TypeError):raise ActionFormatError('Browser arguments must be complete JSON') from None
        try:validate_action_value(arguments,schema)
        except ActionFormatError:
            if schema.get('title') != 'Browser preview ready':
                raise ActionFormatError(str(schema.get('title'))+': use only its offered preparation actions with an explicit entry where required; no previous action is replayed.') from None
            if isinstance(arguments,dict) and arguments.get('action') in {'open','snapshot'}:
                raise ActionFormatError('Current browser preview is already ready and its DOM snapshot was returned. Use an interaction then assert its criterion, reload for deliberate reset, or open a DIFFERENT listed page. Copy the exact offered entry and complete action fields.') from None
            raise


def declared_page_controls(workspace, current_entry):
    """Bounded static discovery, not current DOM state or executed evidence."""
    from html.parser import HTMLParser
    import re
    files=getattr(workspace,'files',None)
    if not callable(files):return {'entries':[], 'scope':'Static page index unavailable'}
    pages=[row['path'] for row in files() if Path(row['path']).suffix.lower() in {'.html','.htm'} and row['path']!=current_entry]
    class Controls(HTMLParser):
        def __init__(self):super().__init__();self.rows=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs);identity=attrs.get('id') or ''
            if tag in {'input','select','textarea','button','a','img'} and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,79}',identity):
                self.rows.append({'id':identity,'tag':tag,'type':attrs.get('type','')})
    entries=[]
    for name in pages[:8]:
        path=workspace.path(name)
        if not path.is_file() or path.stat().st_size>256*1024:continue
        parser=Controls()
        try:parser.feed(path.read_text(encoding='utf8'))
        except (OSError,UnicodeError):continue
        entries.append({'entry':name,'declared_controls':parser.rows[:12]})
    return {'entries':entries,'total_other_pages':len(pages),'truncated':len(entries)<len(pages),
            'scope':'Static HTML declarations only, NOT current DOM or acceptance evidence. Open the intended entry explicitly, then copy its actual selector_catalog. No input values or application data are included.'}
