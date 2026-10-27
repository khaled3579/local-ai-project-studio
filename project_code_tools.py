"""Bounded project search and static diagnostics; never imports project Python."""
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from project_source_index import javascript_index

ROOT=Path(__file__).resolve().parent
NODE=ROOT/'runtime/node/node.exe'
FLAGS=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
SOURCE={'.py','.pyw','.js','.jsx','.ts','.tsx','.mjs','.cjs','.html','.css','.json','.md','.toml','.yaml','.yml','.txt','.sql'}


def diagnostic_path(ws, path=''):
    """Canonicalize an existing diagnostic scope without widening missing paths."""
    if not isinstance(path, str):
        raise ValueError('diagnose_project.path must be text; omit it to inspect the project')
    candidate = (ws.root / path).resolve()
    if candidate == ws.root:
        return ''
    item = ws.path(path)  # Preserve containment, excluded-path and Windows checks.
    if not item.exists():
        raise ValueError('Diagnostic path does not exist. Use list_files and select an existing project file/folder; omit path only when whole-project diagnostics are intended.')
    return item.relative_to(ws.root).as_posix()


def sources(ws, path='', *, allow_directory=False):
    if allow_directory:
        path = diagnostic_path(ws, path)
        if not path:
            rows = ws.files()
        elif ws.path(path).is_dir():
            prefix = path.rstrip('/') + '/'
            rows = [row for row in ws.files() if row['path'].startswith(prefix)]
        else:
            item = ws.path(path)
            rows = [{'path': path, 'bytes': item.stat().st_size}]
    elif path:
        item=ws.path(path)
        if not item.is_file():raise ValueError('اختر ملفًا موجودًا داخل المشروع')
        rows=[{'path':item.relative_to(ws.root).as_posix(),'bytes':item.stat().st_size}]
    else:rows=ws.files()
    return [r for r in rows if Path(r['path']).suffix.lower() in SOURCE and r['bytes']<=1024*1024
            and not Path(r['path']).name.startswith('.env')][:5000]


def search(ws, query, mode='text', path='', limit=30):
    if not isinstance(query,str) or not 1<=len(query)<=200 or mode not in {'text','definitions','references'}:raise ValueError('استعلام بحث غير صالح')
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('حد النتائج بين 1 و100')
    hits=[];total=0;scanned=0;budget=20*1024**2;skipped=0
    for row in sources(ws,path):
        if row['bytes']>budget:skipped+=1;continue
        budget-=row['bytes'];scanned+=1
        text=ws.path(row['path']).read_text(encoding='utf-8',errors='replace');lines=text.splitlines()
        candidates=[]
        if mode=='definitions' and Path(row['path']).suffix in {'.py','.pyw'}:
            try:
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and query.casefold() in node.name.casefold():candidates.append((node.lineno,node.name))
            except SyntaxError:pass
        elif mode=='definitions':
            if Path(row['path']).suffix in {'.js','.jsx','.ts','.tsx','.mjs','.cjs'} and query.casefold() in text.casefold():
                try:
                    definitions, _ = javascript_index(text, Path(row['path']).suffix)
                    candidates = [(d['start_line'], d['symbol']) for d in definitions if query.casefold() in d['symbol'].casefold()]
                except (ValueError, OSError, subprocess.TimeoutExpired):
                    pass
            if not candidates:
                pattern=re.compile(r'\b(?:function|class|interface|type|const|let|var)\s+([\w$]+)')
                for i,line in enumerate(lines):
                    for match in pattern.finditer(line):
                        if query.casefold() in match[1].casefold():candidates.append((i+1,match[1]))
        else:
            pattern=re.compile((r'(?<![\w$])'+re.escape(query)+r'(?![\w$])') if mode=='references' else re.escape(query),re.I)
            candidates=[(i+1,query) for i,line in enumerate(lines) if pattern.search(line)]
        for line,name in candidates:
            total+=1
            if len(hits)>=5000:continue
            hits.append({'path':row['path'],'line':line,'symbol':name,'kind':'definition (AST when source syntax is supported)' if mode=='definitions' else 'lexical '+mode,
                'context':[{'line':i+1,'text':lines[i][:400]} for i in range(max(0,line-2),min(len(lines),line+1))]})
    hits.sort(key=lambda h:(h['symbol'].casefold()!=query.casefold(),h['path'],h['line']))
    return {'matches':hits[:limit],'total_matches':total,'scanned_files':scanned,'skipped_budget':skipped,'truncated':total>limit,'reference_accuracy':'Python and valid JS/TS definitions use syntax ASTs, with lexical fallback for unsupported syntax; references are lexical, not language-server symbol resolution'}


def diagnose(ws,path=''):
    rows=sources(ws,path,allow_directory=True);python=[r['path'] for r in rows if Path(r['path']).suffix in {'.py','.pyw'}]
    script=[r['path'] for r in rows if Path(r['path']).suffix in {'.js','.jsx','.ts','.tsx','.mjs','.cjs'}]
    results=[];start=time.monotonic();env=os.environ.copy();env['PYTHONUTF8']='1'
    from project_browser_tools import private
    directory=private(ws,'tools/diagnostics')
    if python:
        import uuid
        config=directory/(uuid.uuid4().hex+'.json')
        config.write_text(json.dumps({'include':[os.path.relpath(ws.path(p),directory) for p in python], 'typeCheckingMode':'basic','reportMissingImports':'error',
            'exclude':['**/node_modules','**/.venv','**/.local-ai'],'executionEnvironments':[{'root':str(ws.root)}]}),encoding='utf-8')
        args=[str(NODE),str(ROOT/'runtime/assistant/node_modules/pyright/index.js'),'--outputjson','--project',str(config)]
        py=ws.root/'.venv/Scripts/python.exe'
        args+=['--pythonpath',str(py if py.is_file() else Path(sys.executable))]
        try:
            run=subprocess.run(args,cwd=ws.root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,env=env,creationflags=FLAGS)
            data=json.loads(run.stdout)
            results.append({'language':'Python','engine':'pyright','executed':bool(data.get('summary',{}).get('filesAnalyzed')),'summary':data.get('summary'),
                'diagnostics':data.get('generalDiagnostics',[])[:150],'exit_code':run.returncode})
        except (OSError,ValueError,subprocess.TimeoutExpired) as error:results.append({'language':'Python','engine':'pyright','executed':False,'error':str(error)[:1500]})
        finally:config.unlink(missing_ok=True)
    if script:
        request=directory/'typescript-input.json';request.write_text(json.dumps({'root':str(ws.root),'files':script}),encoding='utf-8')
        try:
            run=subprocess.run([str(NODE),str(ROOT/'project_typescript.js'),str(request)],cwd=ws.root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,creationflags=FLAGS)
            data=json.loads(run.stdout);results.append({'language':'JavaScript/TypeScript','engine':'typescript','executed':run.returncode==0,**data})
        except (OSError,ValueError,subprocess.TimeoutExpired) as error:results.append({'language':'JavaScript/TypeScript','engine':'typescript','executed':False,'error':str(error)[:1500]})
    errors=sum(r.get('summary',{}).get('errorCount',0) for r in results)
    return {'success':bool(results) and all(r['executed'] for r in results) and errors==0,'errors':errors,'results':results,
        'seconds':round(time.monotonic()-start,2),'scope':'Static syntax/types/import diagnostics. Does not replace validate_project or executed tests.'}
