"""Bounded source views. Hashes always describe the complete bytes on disk."""
import ast
import difflib
import hashlib
import json
import re
import subprocess
from project_source_index import javascript_index

MAX_FILE_BYTES = 2 * 1024 * 1024


def _symbol_hints(workspace, path, relative, text, definitions, matches, symbol):
    """Bounded discovery hints only: never redirect reads or mint read receipts."""
    names = list(dict.fromkeys(row['symbol'] for row in definitions))
    query = symbol.replace('.prototype.', '.')[:240]
    selected = list(dict.fromkeys([row['symbol'] for row in matches]
                                + difflib.get_close_matches(query, names, n=5, cutoff=0.35)))[:5]
    candidates = []
    for name in selected:
        for row in definitions:
            if row['symbol'] == name:
                # For overloads or ambiguous definitions, line ranges remain
                # unambiguous even when the same symbol spelling is repeated.
                arguments = {'path':relative, 'start_line':row['start_line'],
                             'end_line':row.get('end_line', row['start_line']), 'max_chars':12000}
                candidates.append({'symbol':name[:240], 'read_arguments':arguments})
                if len(candidates) == 5:
                    break
        if len(candidates) == 5:
            break
    modules = []
    references = re.findall(r'''(?:\bfrom\s*|\brequire\s*\(\s*)['"](\.[^'"\r\n]{1,240})['"]''', text)
    target = query.split('.')[0].casefold()
    references = sorted(dict.fromkeys(references), key=lambda name: (target not in name.casefold(), name))
    for reference in references[:32]:
        candidate = path.parent / reference
        variants = [candidate] if candidate.suffix else [candidate.with_suffix(suffix) for suffix in ('.js','.mjs','.cjs','.ts','.tsx','.jsx')]
        for variant in variants:
            try:
                name = variant.resolve().relative_to(workspace.root).as_posix()
                allowed = workspace.path(name)
                if allowed.is_file() and name not in modules:
                    modules.append(name)
            except (OSError, ValueError):
                continue
            if len(modules) == 5:
                break
        if len(modules) == 5:
            break
    return {'path':relative, 'requested_symbol':symbol[:240], 'matching_definitions':len(matches),
            'candidate_definitions':candidates, 'existing_relative_module_paths':modules,
            'action':'Review these exact indexed definitions or existing imported-module paths, then request an explicit read. '
                     'Hints do not establish a read receipt, select an alias, resolve an export, or approve a source edit.'}


def _definitions(text, suffix):
    if suffix.lower() in {'.py', '.pyw'}:
        tree = ast.parse(text)
        rows = []

        def visit(node, prefix=''):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = prefix + child.name
                    start = min([child.lineno, *[d.lineno for d in child.decorator_list]])
                    rows.append({'symbol': name, 'start_line': start, 'end_line': child.end_lineno})
                    visit(child, name + '.')
                else:
                    visit(child, prefix)

        visit(tree)
        return rows, 'Python AST'
    if suffix.lower() in {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx'}:
        return javascript_index(text, suffix)
    pattern = re.compile(r'^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class|interface|type|const|let|var)\s+([\w$]+)')
    return ([{'symbol': m[1], 'start_line': i + 1} for i, line in enumerate(text.splitlines())
             if (m := pattern.match(line))], 'lexical index; use line ranges to read bodies')


def read_view(workspace, path, *, start_line=None, end_line=None, symbol=None,
              outline=False, max_chars=None, offset=0):
    p = workspace.path(path)
    with p.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Source file exceeds the 2 MiB read limit')
    text = raw.decode('utf-8-sig')
    base = {'path': p.relative_to(workspace.root).as_posix(),
            'sha256': hashlib.sha256(raw).hexdigest()}
    # Python callers and the editor retain the exact legacy full-read contract.
    if start_line is None and end_line is None and symbol is None and not outline and max_chars is None and offset == 0:
        return {**base, 'content': text}
    for key, value in (('start_line', start_line), ('end_line', end_line)):
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(key + ' must be a positive integer')
    if type(outline) is not bool or type(offset) is not int or offset < 0:
        raise ValueError('Invalid outline or offset')
    if outline and (start_line is not None or end_line is not None):
        raise ValueError('Use outline OR a line range. For definitions send {"path": "...", "outline": true} without start_line/end_line. For source lines omit outline or set it to false.')
    limit = 12000 if max_chars is None else max_chars
    if type(limit) is not int or not 1 <= limit <= 32000:
        raise ValueError('max_chars must be between 1 and 32000')
    lines = text.splitlines(keepends=True)
    total = len(lines)
    symbol_start = None
    if outline or symbol is not None:
        if symbol is not None and (not isinstance(symbol, str) or not symbol or outline or start_line is not None or end_line is not None):
            raise ValueError('Use a symbol OR a line range OR outline. Choose exactly one selector: a nonempty symbol with no start_line/end_line and outline=false or omitted; OR start_line/end_line with symbol omitted and outline=false or omitted; OR outline=true with symbol and line numbers omitted. Do not repeat the rejected arguments.')
        try:
            definitions, accuracy = _definitions(text, p.suffix)
        except (SyntaxError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            if symbol is not None:
                raise ValueError('Cannot select a symbol: invalid Python or unavailable source index; use search_project and line ranges. ' + str(error)) from None
            definitions, accuracy = [], 'Invalid Python or unavailable source index; use search_project and line ranges'
        if outline:
            # Index pages have the same bounded, lossless pagination as source views.
            selected = json.dumps({'definitions': definitions, 'accuracy': accuracy}, ensure_ascii=False)
            view = {'outline': True}
        else:
            matches = [d for d in definitions if d['symbol'] == symbol or d['symbol'].split('.')[-1] == symbol]
            exact = [d for d in matches if d['symbol'] == symbol]
            matches = exact or matches
            implementations = [d for d in matches if d.get('implementation')]
            if implementations:
                matches = implementations
            if len(matches) != 1 or 'end_line' not in matches[0]:
                hints = _symbol_hints(workspace, p, base['path'], text, definitions, matches, symbol)
                raise ValueError('Symbol absent, ambiguous or not supported; use outline/search_project then line ranges. '
                                 + json.dumps(hints, ensure_ascii=False))
            start_line, end_line = matches[0]['start_line'], matches[0]['end_line']
            if 'start_utf16' in matches[0]:
                utf16 = text.encode('utf-16-le')
                symbol_start = len(utf16[:matches[0]['start_utf16'] * 2].decode('utf-16-le'))
                symbol_end = len(utf16[:matches[0]['end_utf16'] * 2].decode('utf-16-le'))
                selected = text[symbol_start:symbol_end]
            else:
                selected = ''.join(lines[start_line - 1:end_line])
            view = {'start_line': start_line, 'end_line': end_line}
    else:
        start_line = start_line or 1
        end_line = end_line if end_line is not None else total
        if start_line > max(1, total) or end_line < start_line and total:
            raise ValueError('Line range outside source file')
        end_line = min(end_line, total)
        selected = ''.join(lines[start_line - 1:end_line])
        view = {'start_line': start_line, 'end_line': end_line}
    if offset > len(selected):
        raise ValueError('Offset outside selected view')
    content = selected[offset:offset + limit]
    more = offset + len(content) < len(selected)
    next_view = {'symbol': symbol} if symbol is not None else view
    next_read = {'path': base['path'], **next_view, 'offset': offset + len(content), 'max_chars': limit} if more else None
    if outline:
        char_start, char_end = None, None
    else:
        assert start_line is not None
        char_start = (symbol_start if symbol_start is not None else sum(len(line) for line in lines[:start_line - 1])) + offset
        char_end = char_start + len(content)
    return {**base, **view, 'content': content, 'total_lines': total, 'total_chars': len(text),
            'char_start': char_start, 'char_end': char_end,
            'offset': offset, 'more': more, 'next_read': next_read, 'selected_chars': len(selected)}


def observe_read(coverage, value):
    """Track complete-file coverage across pages, invalidated by a changed SHA."""
    path, sha = value['path'], value['sha256']
    row = coverage.get(path)
    if not row or row['sha256'] != sha:
        row = coverage[path] = {'sha256': sha, 'spans': [], 'complete': False}
    if value.get('outline'):
        return
    if 'total_chars' not in value:
        row['complete'] = True  # Legacy full reads from an older saved task.
        return
    spans = sorted([*row['spans'], [value['char_start'], value['char_end']]])
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    row['spans'] = merged
    row['complete'] = merged == [[0, value['total_chars']]]


def require_complete_read(coverage, path, sha, *, legacy_read=False):
    row = coverage.get(path)
    if row is None and legacy_read:
        return
    if row is None or row['sha256'] != sha or not row['complete']:
        raise ValueError('A complete current source read is required (an excerpt or a write receipt is insufficient). Use patch_file for a targeted change, or read every source page before replacing the whole file.')


def observe_mutation(receipts, coverage, value):
    """Track our committed bytes for the next patch, never grant a full read."""
    if not isinstance(value, dict) or not value.get('saved') or value.get('error'):
        return
    path, sha = value.get('saved'), value.get('sha256')
    if not isinstance(path, str) or not isinstance(sha, str):
        return
    receipts[path] = sha
    coverage[path] = {'sha256': sha, 'spans': [], 'complete': False}
