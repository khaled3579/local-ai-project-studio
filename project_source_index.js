'use strict';
/* Parse only. Project code is never imported, evaluated or transpiled to run. */
const ts = require('./runtime/assistant/node_modules/typescript');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  if (Buffer.byteLength(input, 'utf8') > 8 * 1024 * 1024) {
    process.stderr.write('Source-index input exceeds limit'); process.exit(1);
  }
});
process.stdin.on('end', () => {
  try {
    const {content, suffix} = JSON.parse(input);
    if (typeof content !== 'string' || typeof suffix !== 'string') throw Error('Invalid index request');
    const kind = ({'.js':ts.ScriptKind.JS,'.mjs':ts.ScriptKind.JS,'.cjs':ts.ScriptKind.JS,
      '.jsx':ts.ScriptKind.JSX,'.ts':ts.ScriptKind.TS,'.tsx':ts.ScriptKind.TSX})[suffix];
    if (kind === undefined) throw Error('Unsupported source language');
    const source = ts.createSourceFile('source'+suffix, content, ts.ScriptTarget.Latest, true, kind);
    if (source.parseDiagnostics.length) {
      process.stdout.write(JSON.stringify({error:'Invalid source syntax', diagnostics:source.parseDiagnostics.slice(0,8).map(d=>({
        line:source.getLineAndCharacterOfPosition(d.start||0).line+1,
        message:ts.flattenDiagnosticMessageText(d.messageText,' ')}))}));
      return;
    }
    const definitions = [];
    function nameOf(node) {
      if (!node) return '';
      if (ts.isIdentifier(node) || ts.isStringLiteral(node) || ts.isNumericLiteral(node)) return node.text;
      return '';
    }
    function add(node,name,prefix) {
      if (!name) return prefix;
      const qualified = prefix ? prefix+'.'+name : name;
      const start=Math.min(node.getStart(source),...(node.jsDoc||[]).map(comment=>comment.pos));
      definitions.push({symbol:qualified,start_line:source.getLineAndCharacterOfPosition(start).line+1,
        end_line:source.getLineAndCharacterOfPosition(Math.max(node.getStart(source),node.getEnd()-1)).line+1,
        start_utf16:start,end_utf16:node.getEnd(),
        implementation:!!node.body || !!node.initializer});
      return qualified;
    }
    function visit(node,prefix='') {
      let childPrefix=prefix;
      if (ts.isClassDeclaration(node) || ts.isClassExpression(node) || ts.isFunctionDeclaration(node) ||
          ts.isMethodDeclaration(node) || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node) ||
          ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node) || ts.isEnumDeclaration(node)) {
        childPrefix=add(node,nameOf(node.name),prefix);
      } else if (ts.isConstructorDeclaration(node)) {
        childPrefix=add(node,'constructor',prefix);
      } else if (ts.isVariableDeclaration(node)) {
        const initializer=node.initializer;
        if (!prefix || initializer && (ts.isArrowFunction(initializer)||ts.isFunctionExpression(initializer)||ts.isObjectLiteralExpression(initializer)||ts.isClassExpression(initializer))) {
          // Include const/let/var when the declaration is the only one in its statement.
          const list=node.parent,statement=list?.parent;
          const view=ts.isVariableDeclarationList(list)&&list.declarations.length===1&&ts.isVariableStatement(statement)?statement:node;
          childPrefix=add(view,nameOf(node.name),prefix);
        }
      } else if (ts.isPropertyAssignment(node) || ts.isPropertyDeclaration(node)) {
        if(node.initializer && (ts.isArrowFunction(node.initializer)||ts.isFunctionExpression(node.initializer)||ts.isObjectLiteralExpression(node.initializer)))
          childPrefix=add(node,nameOf(node.name),prefix);
      }
      ts.forEachChild(node,child=>visit(child,childPrefix));
    }
    visit(source);
    process.stdout.write(JSON.stringify({definitions,accuracy:'TypeScript compiler AST (syntax only; no module execution)'}));
  } catch(error) { process.stderr.write(error.message); process.exitCode=1; }
});
