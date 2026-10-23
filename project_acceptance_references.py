"""Read references in an already executed Node test without executing its source."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


PARSER = r'''
const ts = require(process.argv[1]);
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const root = ts.createSourceFile(input.file, input.source, ts.ScriptTarget.Latest, true);
if (root.parseDiagnostics.length) throw Error('Test source has parse diagnostics');
const result = [];
const literal = n => n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) ? n.text : null;
const aliases = new Map();
const namespaces = new Set();
function imported(local, original) {
  if (['test', 'it', 'default'].includes(original)) aliases.set(local, 'test');
  if (['describe', 'suite'].includes(original)) aliases.set(local, 'suite');
}
function isNodeRequire(n) {
  return n && ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'require'
    && literal(n.arguments[0]) === 'node:test';
}
for (const statement of root.statements) {
  if (ts.isImportDeclaration(statement) && literal(statement.moduleSpecifier) === 'node:test' && statement.importClause) {
    const clause = statement.importClause;
    if (clause.name) imported(clause.name.text, 'default');
    const bindings = clause.namedBindings;
    if (bindings && ts.isNamespaceImport(bindings)) namespaces.add(bindings.name.text);
    if (bindings && ts.isNamedImports(bindings)) for (const element of bindings.elements) {
      imported(element.name.text, (element.propertyName || element.name).text);
    }
  }
  if (ts.isVariableStatement(statement)) for (const declaration of statement.declarationList.declarations) {
    if (!isNodeRequire(declaration.initializer)) continue;
    if (ts.isIdentifier(declaration.name)) {
      imported(declaration.name.text, 'default');
      namespaces.add(declaration.name.text);
    }
    if (ts.isObjectBindingPattern(declaration.name)) for (const element of declaration.name.elements) {
      if (!element.dotDotDotToken && ts.isIdentifier(element.name)) {
        imported(element.name.text, (element.propertyName || element.name).text);
      }
    }
  }
}
function callName(node) {
  if (ts.isIdentifier(node)) return node.text;
  if (ts.isPropertyAccessExpression(node)) return callName(node.expression) + '.' + node.name.text;
  return '';
}
function registration(expression, contexts) {
  let name = callName(expression);
  name = name.replace(/\.(?:only|skip|todo)$/, '');
  if (aliases.has(name)) return aliases.get(name);
  const parts = name.split('.');
  if (parts.length === 2 && namespaces.has(parts[0])) {
    if (['test', 'it'].includes(parts[1])) return 'test';
    if (['describe', 'suite'].includes(parts[1])) return 'suite';
  }
  if (parts.length === 2 && contexts.has(parts[0]) && parts[1] === 'test') return 'test';
  return null;
}
function walk(node, suites, contexts) {
  if (ts.isCallExpression(node)) {
    const kind = registration(node.expression, contexts);
    const title = literal(node.arguments[0]);
    const callback = [...node.arguments].reverse().find(n => ts.isArrowFunction(n) || ts.isFunctionExpression(n));
    if (title !== null && callback) {
      if (kind === 'suite') {
        walk(callback.body, [...suites, title], contexts);
        return;
      }
      if (kind === 'test') {
        const identifiers = new Set();
        const nestedContexts = new Set(contexts);
        const parameter = callback.parameters[0];
        if (parameter && ts.isIdentifier(parameter.name)) nestedContexts.add(parameter.name.text);
        function references(n) {
          // A child registration has its own receipt. Its assertions cannot
          // provide target references for the parent's successful receipt.
          if (ts.isCallExpression(n) && registration(n.expression, nestedContexts)) return;
          if (ts.isIdentifier(n)) identifiers.add(n.text);
          ts.forEachChild(n, references);
        }
        references(callback.body);
        result.push({name: title, suite: suites.join('.'), identifiers: [...identifiers],
                     line: root.getLineAndCharacterOfPosition(node.getStart(root)).line + 1});
        walk(callback.body, [...suites, title], nestedContexts);
        return;
      }
    }
  }
  ts.forEachChild(node, child => walk(child, suites, contexts));
}
walk(root, [], new Set());
process.stdout.write(JSON.stringify(result));
'''


def node_reference(workspace, report, identity):
    """Source references supplement an exact receipt, never manufacture a pass."""
    relative = identity.get('file')
    name = identity.get('name')
    if not isinstance(relative, str) or not isinstance(name, str):
        return {'identifiers': [], 'diagnostic': 'Missing exact runner file/name identity'}
    try:
        path = workspace.path(relative)
        if path.suffix not in {'.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx'} or path.stat().st_size > 2 * 1024 * 1024:
            return {'identifiers': [], 'diagnostic': 'Unsupported or oversized test source'}
        raw = path.read_bytes()
        source_sha = hashlib.sha256(raw).hexdigest()
        runs = tuple(str(c.get('validation_run_id')) for c in report.get('checks', []))
        key = (report.get('fingerprint'), runs, relative, source_sha)
        cache = getattr(workspace, '_acceptance_node_source_references', {})
        parsed = cache.get(key)
        if parsed is None:
            root = Path(__file__).resolve().parent
            node = root / 'runtime/node/node.exe'
            parser = root / 'runtime/assistant/node_modules/typescript/lib/typescript.js'
            env = dict(os.environ, NODE_OPTIONS='')
            env.pop('NODE_PATH', None)
            result = subprocess.run([str(node), '-e', PARSER, str(parser)],
                                    input=json.dumps({'file': relative, 'source': raw.decode('utf-8-sig')}),
                                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    timeout=5, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                return {'identifiers': [], 'diagnostic': 'Static test reference parser failed: ' + result.stderr[-1200:]}
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, list):
                return {'identifiers': [], 'diagnostic': 'Invalid static parser result'}
            cache[key] = parsed
            if len(cache) > 128:
                cache.pop(next(iter(cache)))
            workspace._acceptance_node_source_references = cache
        # Always match all runner identity fields, including unique leaf names.
        # A contradictory suite is not a license to select the only callback.
        matching = [row for row in parsed if row.get('name') == name and row.get('suite') == identity.get('suite')]
        if len(matching) != 1:
            return {'identifiers': [], 'diagnostic': 'Test callback is absent, dynamic or ambiguous; inspect the exact assertion'}
        row = matching[0]
        return {'identifiers': row['identifiers'], 'file': relative, 'line': row['line'],
                'source_sha256': source_sha, 'scope': 'Static references in the exact executed test callback; assertion meaning still requires review.'}
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        return {'identifiers': [], 'diagnostic': 'Cannot inspect static test references: ' + str(error)[:1200]}
