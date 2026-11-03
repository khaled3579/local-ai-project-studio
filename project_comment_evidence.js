'use strict';
// Parse project text only; never import or execute it.
const ts = require('./runtime/assistant/node_modules/typescript');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  input += chunk;
  if (Buffer.byteLength(input, 'utf8') > 8 * 1024 * 1024) process.exit(1);
});
process.stdin.on('end', () => {
  try {
    const {content, suffix} = JSON.parse(input);
    const kind = ({'.js':ts.ScriptKind.JS,'.mjs':ts.ScriptKind.JS,'.cjs':ts.ScriptKind.JS,
      '.jsx':ts.ScriptKind.JSX,'.ts':ts.ScriptKind.TS,'.tsx':ts.ScriptKind.TSX})[suffix];
    if (typeof content !== 'string' || kind === undefined) throw Error('Unsupported source');
    const root = ts.createSourceFile('source'+suffix, content, ts.ScriptTarget.Latest, true, kind);
    if (root.parseDiagnostics.length) throw Error('Cannot prove comment preservation for invalid syntax');
    const ranges = new Map();
    function collect(items) {
      for (const item of items || []) ranges.set(item.pos + ':' + item.end, item);
    }
    function visit(node) {
      const children = node.getChildren(root);
      if (node.kind <= ts.SyntaxKind.LastToken && node.kind !== ts.SyntaxKind.JsxText) {
        // Only token-leading trivia is comment space. Text after a JSX tag or
        // inside a template must never be scanned as JavaScript comments.
        const start = node.getStart(root);
        for (const ranges of [ts.getLeadingCommentRanges(content,node.getFullStart()),
                              ts.getTrailingCommentRanges(content,node.getFullStart())]) {
          collect((ranges || []).filter(range => range.end <= start));
        }
      }
      for (const child of children) visit(child);
    }
    visit(root);
    const ordered = [...ranges.values()].sort((a,b) => a.pos-b.pos || b.end-a.end);
    let end = -1;
    const comments = [];
    for (const range of ordered) {
      if (range.pos < end) continue;
      comments.push(content.slice(range.pos, range.end)); end = range.end;
    }
    process.stdout.write(JSON.stringify({comments}));
  } catch (error) { process.stdout.write(JSON.stringify({error:String(error.message)})); }
});
