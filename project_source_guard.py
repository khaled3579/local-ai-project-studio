"""Reject historical placeholders and malformed JavaScript before committing."""
import ast
from collections import Counter
import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import uuid


def baseline_signature(version):
    return hashlib.sha256(json.dumps(version, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def bind_baseline(workspace, version_id):
    return {'id':version_id, 'sha256':baseline_signature(workspace.history.get(version_id))}


def baseline_current(workspace):
    pin = getattr(workspace, 'source_guard_baseline', None)
    if pin is None:
        return True
    try:
        return baseline_signature(workspace.history.get(pin['id'])) == pin['sha256']
    except (OSError, ValueError, KeyError, TypeError):
        return False


def validation_current(workspace, report):
    pin = getattr(workspace, 'source_guard_baseline', None)
    if pin is None:
        return True
    checks = [c for c in (report or {}).get('checks', []) if c.get('name') == 'source_definitions']
    return bool(baseline_current(workspace) and (report or {}).get('source_guard_baseline_sha256') == pin['sha256']
                and len(checks) == 1 and checks[0].get('passed') is True)


def definition_check(workspace):
    """Check changed sources against the immutable task-start history snapshot.

    Commands and external editors cannot bypass delivery by avoiding file tools.
    Existing intentional shadowing is preserved relative to this task baseline.
    """
    pin = getattr(workspace, 'source_guard_baseline', None)
    result = {'name':'source_definitions', 'passed':False, 'validation_run_id':uuid.uuid4().hex,
              'baseline_sha256':(pin or {}).get('sha256'), 'baseline_version':(pin or {}).get('id'), 'checked_files':[], 'errors':[]}
    try:
        if pin is None:
            raise ValueError('Source-definition baseline is missing; delivery is blocked.')
        version = workspace.history.get(pin['id'])
        if baseline_signature(version) != pin['sha256']:
            raise ValueError('Source-definition baseline changed; delivery is blocked: ' + pin['id'])
        original = {r['path']:r for r in version['files']}
        cache = getattr(workspace, '_source_definition_cache', None)
        if cache is None:
            cache = workspace._source_definition_cache = {}
        result['cached_files'] = []
        for item in workspace.files():
            path = workspace.path(item['path'])
            if path.suffix.lower() not in {'.py','.pyw','.js','.mjs','.cjs'}:
                continue
            raw = path.read_bytes()
            old = original.get(item['path'])
            current_sha = hashlib.sha256(raw).hexdigest()
            if old and current_sha == old['sha256']:
                continue
            result['checked_files'].append(item['path'])
            try:
                previous = workspace.history.blob(old) if old else None
                key = (pin['sha256'], item['path'], (old or {}).get('sha256'), current_sha)
                if key in cache:
                    result['cached_files'].append(item['path'])
                    continue
                check_source(path, raw.decode('utf-8-sig'), previous)
                cache[key] = True
                while len(cache) > 512:
                    cache.pop(next(iter(cache)))
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                result['errors'].append({'path':item['path'], 'error':str(error)})
        result['passed'] = not result['errors']
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['error'] = str(error)
    return result


def python_definitions(content):
    """Count concrete undecorated definitions in each lexical statement list.

    Decorated overloads/properties and alternative conditional branches are
    deliberately separate. Existing shadowing is grandfathered, never removed.
    """
    counts = Counter()
    def visit(node, scope):
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                siblings = Counter()
                for child in value:
                    if not isinstance(child, ast.AST):
                        continue
                    label = (type(child).__name__, getattr(child, 'name', ''))
                    occurrence = siblings[label]
                    siblings[label] += 1
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.decorator_list:
                        counts[(scope, field, child.name)] += 1
                    visit(child, scope + ((field, *label, occurrence),))
            elif isinstance(value, ast.AST):
                visit(value, scope + ((field,),))
    try:
        visit(ast.parse(content), ())
    except SyntaxError:
        # This guard is about new shadowing, not changing partial-Python editing.
        return None
    return counts


JS_DEFINITIONS = r'''
const fs = require('node:fs');
const ts = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function inventory(text) {
  const root = ts.createSourceFile(input.path, text, ts.ScriptTarget.Latest, true);
  const result = {};
  function keyName(name) {
    if (!name) return '';
    if (ts.isComputedPropertyName(name) && (ts.isStringLiteral(name.expression) || ts.isNumericLiteral(name.expression))) return name.expression.text;
    return typeof name.text === 'string' ? name.text : name.getText(root);
  }
  function walk(node, scope) {
    const children = ts.isSourceFile(node) || ts.isBlock(node) ? node.statements
      : ts.isClassDeclaration(node) || ts.isClassExpression(node) ? node.members
      : ts.isObjectLiteralExpression(node) ? node.properties : null;
    if (children) {
      const occurrences = {};
      const classScope = ts.isClassDeclaration(node) || ts.isClassExpression(node);
      for (const child of children) {
        const name = keyName(child.name);
        const binding = classScope ? ((child.modifiers || []).some(m => m.kind === ts.SyntaxKind.StaticKeyword) ? 'static:' : 'instance:') + name : name;
        const functionProperty = ts.isPropertyAssignment(child) && (ts.isArrowFunction(child.initializer) || ts.isFunctionExpression(child.initializer));
        if ((((ts.isFunctionDeclaration(child) || ts.isMethodDeclaration(child)) && child.body) || functionProperty) && name) {
          const key = JSON.stringify([scope, binding]);
          result[key] = (result[key] || 0) + 1;
        }
        const label = child.kind + ':' + binding;
        const ordinal = occurrences[label] || 0;
        occurrences[label] = ordinal + 1;
        walk(child, scope + '/' + label + ':' + ordinal);
      }
    } else {
      let ordinal = 0;
      ts.forEachChild(node, child => { walk(child, scope + '/' + child.kind + ':' + ordinal++); });
    }
  }
  walk(root, 'root');
  return result;
}
process.stdout.write(JSON.stringify([inventory(input.old), inventory(input.content)]));
'''


def reject_new_duplicates(before, after):
    if after is None:
        return
    before = before or {}
    added = [str(key) for key, count in after.items() if count > max(1, before.get(key, 0))]
    if added:
        raise ValueError('Duplicate function definition rejected before saving; existing file preserved. '
                         'Update the existing definition instead of appending a shadowing copy. Scopes: ' + ', '.join(added[:6]))


def syntax_diagnostic(message, content, base_sha256):
    """Rejected proposal only: it has never replaced the current source."""
    match = re.search(r':(\d+)(?::\d+)?(?:\r?\n|$)', message)
    line = int(match[1]) if match else None
    lines = content.splitlines(); start = max(0,(line or 1)-6)
    excerpt = '\n'.join(f'{i+1}: {lines[i]}' for i in range(start,min(len(lines),start+11)))[:4000]
    return {'candidate_saved':False,'base_sha256':base_sha256,
            'candidate_sha256':hashlib.sha256(content.encode('utf8')).hexdigest(),
            'candidate_line':line,'candidate_excerpt':excerpt,
            'next_action':'Correct the REJECTED proposal from the preserved failed call; these lines are NOT current source. No edit was committed. Read current source for exact patch anchors/SHA, preserve existing behavior, then submit one corrected valid change. Browser assertions or validation on the unchanged old file cannot test this proposal. Never patch a rejected excerpt as though it were the current file. Duplicate exports must use either declaration exports or an export list for the same name, not both. Existing retry and acceptance gates remain in force.'}


def syntax_repair_checkpoint(messages, workspace):
    calls = {call.get('id'):call.get('function') or {} for message in messages
             for call in message.get('tool_calls',[]) if message.get('role')=='assistant'}
    seen = set()
    for message in reversed(messages):
        function = calls.get(message.get('tool_call_id'))
        if message.get('role')!='tool' or not function or function.get('name') not in {'write_file','patch_file'}:
            continue
        try:
            args=json.loads(function.get('arguments') or '{}'); value=json.loads(message.get('content') or '{}')
            if not isinstance(args,dict) or not isinstance(value,dict):continue
            name=args['path']; path=workspace.path(name)
            if name in seen:continue
            if value.get('saved') and value.get('sha256') and not value.get('error'):
                seen.add(name);continue
            if not str(value.get('error','')).startswith('JavaScript syntax rejected;'):continue
            seen.add(name)
            current=path.read_bytes() if path.exists() else None
            sha=hashlib.sha256(current).hexdigest() if current is not None else None
            base=(value.get('syntax_repair') or {}).get('base_sha256',args.get('expected_sha256'))
            if sha!=base or sha is not None and not base:continue
            proposal=args.get('content') if function['name']=='write_file' else None
            if proposal is None and current is not None:
                source=current.decode('utf8').replace('\r\n','\n'); old=args.get('old_text','').replace('\r\n','\n')
                if old and source.count(old)==1:proposal=source.replace(old,args.get('new_text','').replace('\r\n','\n'),1)
            diagnostic=value.get('syntax_repair') or (syntax_diagnostic(value['error'],proposal,base) if isinstance(proposal,str) else None)
            if diagnostic:return {'path':name,**diagnostic}
        except (ValueError,TypeError,KeyError,OSError):
            continue
    return None


def syntax_recovery_tools(tools, checkpoint):
    if not checkpoint:return tools
    withheld={'browser_project','validate_project','acceptance_plan','link_acceptance_evidence','run_project','diagnose_project','export_project'}
    return [entry for entry in tools if entry['function']['name'] not in withheld]


def check_source(path, content, old_bytes=None):
    if path.suffix.lower() not in {'.py','.pyw','.js','.mjs','.cjs','.ts','.tsx','.jsx'}:
        return
    if re.fullmatch(r'\s*\[Older committed source omitted; payload SHA256=[0-9a-f]{64}\. Read current file before editing\.\]\s*', content):
        raise ValueError('Historical context placeholder is not source code. Read current file and provide actual complete source or an exact patch. No file changed.')
    previous = old_bytes.decode('utf-8-sig') if old_bytes is not None else ''
    if path.suffix.lower() in {'.py', '.pyw'}:
        reject_new_duplicates(python_definitions(previous), python_definitions(content))
    if path.suffix.lower() not in {'.js','.mjs','.cjs'}:
        return
    node=shutil.which('node')
    if not node:
        raise ValueError('Node.js is required to check JavaScript before saving; no file changed.')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.source-check-'+uuid.uuid4().hex+path.suffix)
    try:
        temporary.write_text(content,encoding='utf-8')
        result=subprocess.run([node,'--check',str(temporary)],capture_output=True,text=True,
                              encoding='utf-8',errors='replace',timeout=15,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            error=ValueError('JavaScript syntax rejected; existing file preserved. '+result.stderr[-4000:])
            error.syntax_repair=syntax_diagnostic(result.stderr,content,hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None)
            raise error
        parser = Path(__file__).resolve().parent/'runtime/assistant/node_modules/typescript/lib/typescript.js'
        parsed = subprocess.run([node, '-e', JS_DEFINITIONS, str(parser)],
                                input=json.dumps({'path':str(path), 'old':previous, 'content':content}),
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if parsed.returncode:
            raise ValueError('JavaScript definition check failed; existing file preserved. '+parsed.stderr[-2000:])
        before, after = json.loads(parsed.stdout)
        reject_new_duplicates(before, after)
    finally:
        temporary.unlink(missing_ok=True)
