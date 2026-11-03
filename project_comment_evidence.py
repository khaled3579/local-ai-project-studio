"""Exact original comment tokens in order, bound to immutable task-start bytes.

Unchanged files prove preservation by hash. Changed unsupported source languages
fail closed. Additional comments are allowed; deleted/changed/duplicated-original
comments cannot be satisfied by a model declaration or an unrelated test.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tokenize

SOURCE = {'.py','.pyw','.js','.mjs','.cjs','.jsx','.ts','.tsx','.css','.html','.htm',
          '.vue','.svelte','.sql','.c','.h','.cpp','.hpp','.java','.go','.rs','.cs', '.sh','.ps1'}

def clause(text):
    return bool(re.fullmatch(r'preserve (?:the )?(?:existing|original) (?:reference )?comments',
                            text.strip().rstrip('.;؛!?؟').strip(), re.I))

def comments(content, suffix):
    if suffix in {'.py','.pyw'}:
        return [item.string for item in tokenize.generate_tokens(io.StringIO(content).readline)
                if item.type == tokenize.COMMENT]
    if suffix not in {'.js','.mjs','.cjs','.jsx','.ts','.tsx'}:
        raise ValueError('Changed source language requires separate comment-preservation review: '+suffix)
    root = Path(__file__).resolve().parent
    process = subprocess.run([str(root/'runtime/node/node.exe'),str(root/'project_comment_evidence.js')],
        input=json.dumps({'content':content,'suffix':suffix}),capture_output=True,text=True,
        encoding='utf-8',timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if process.returncode:raise ValueError('Comment parser failed')
    result=json.loads(process.stdout)
    if 'error' in result:raise ValueError(result['error'])
    return result['comments']

def retained(original, current):
    cursor=iter(current)
    return all(any(item == expected for item in cursor) for expected in original)

def evidence(workspace, job):
    from project_source_guard import baseline_current
    proof={'passed':False,'checked_files':[],'unchanged_files':[], 'errors':[],
           'scope':'Original source comment tokens retained exactly in original order; additional comments allowed. Changed unsupported languages block this receipt.'}
    pin=getattr(workspace,'source_guard_baseline',None)
    if not pin or pin.get('id')!=job.get('base_version') or not baseline_current(workspace):
        proof['errors'].append('Missing or changed immutable task baseline');return proof
    fingerprint=workspace.fingerprint(force=True)
    key=(pin['sha256'],fingerprint)
    cached=getattr(workspace,'_comment_preservation_cache',None)
    if cached and cached[0]==key:return json.loads(json.dumps(cached[1]))
    try:
        for item in workspace.history.get(pin['id'])['files']:
            path=workspace.path(item['path']);suffix=path.suffix.lower()
            raw=path.read_bytes() if path.is_file() else None
            if suffix not in SOURCE:
                # Unknown formats may contain comments too (JSONC, YAML, etc.).
                # Identical bytes are safe; never silently skip a changed file.
                if raw is not None and hashlib.sha256(raw).hexdigest()==item['sha256']:continue
                if suffix=='.json' and raw is not None:
                    try:
                        json.loads(workspace.history.blob(item).decode('utf-8-sig'))
                        json.loads(raw.decode('utf-8-sig'))
                        continue  # Strict JSON has no comment tokens.
                    except (ValueError,UnicodeError):pass
                proof['errors'].append({'path':item['path'],'error':'Changed unsupported file format requires separate comment-preservation review'})
                continue
            if raw is not None and hashlib.sha256(raw).hexdigest()==item['sha256']:
                proof['unchanged_files'].append(item['path']);continue
            try:
                before=comments(workspace.history.blob(item).decode('utf-8-sig'),suffix)
                after=comments(raw.decode('utf-8-sig'),suffix) if raw is not None else []
                proof['checked_files'].append({'path':item['path'],'original_comments':len(before),'current_comments':len(after)})
                if not retained(before,after):raise ValueError('Original comment tokens missing, modified or reordered')
            except (ValueError,UnicodeError,tokenize.TokenError,IndentationError,subprocess.SubprocessError) as error:
                proof['errors'].append({'path':item['path'],'error':str(error)})
        if workspace.fingerprint(force=True)!=fingerprint:raise ValueError('Sources changed while checking comments')
        proof['passed']=not proof['errors']
        proof.update(baseline_sha256=pin['sha256'],fingerprint=fingerprint)
        workspace._comment_preservation_cache=(key,json.loads(json.dumps(proof)))
    except (OSError,ValueError,KeyError,TypeError) as error:
        proof['errors'].append(str(error))
    return proof
