"""Lossless execution ledger, bounded model-facing projection of old reads."""
import json
import hashlib


def project_messages(messages, keep_reads=4, read_chars=24000, keep_mutations=2):
    """Only omit older source bodies; never mutate durable messages or tool pairs."""
    read_ids = {call.get('id') for message in messages for call in message.get('tool_calls', [])
                if (call.get('function') or {}).get('name') == 'read_file'}
    reads = []
    for index, message in enumerate(messages):
        if message.get('role') != 'tool' or message.get('tool_call_id') not in read_ids or not isinstance(message.get('content'), str):
            continue
        try:
            value = json.loads(message['content'])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get('content'), str) and value.get('sha256') and value.get('path'):
            reads.append((index, value))
    result = list(messages)
    last_mutations = {}
    for index, message in enumerate(messages):
        if message.get('role') != 'tool':
            continue
        try:
            value = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get('saved'), str) and value.get('sha256') and not value.get('error'):
            last_mutations[value['saved']] = index
    retained, used = set(), 0
    for position, (index, value) in enumerate(reversed(reads)):
        size = len(value['content'])
        if position < min(2, max(1, keep_reads)) or position < max(1, keep_reads) and used + size <= read_chars:
            retained.add(index)
            used += size
    for index, value in reads:
        if index in retained and index > last_mutations.get(value['path'], -1):
            continue
        receipt = {k: value[k] for k in ('path', 'sha256', 'start_line', 'end_line', 'outline', 'offset', 'more', 'next_read') if k in value}
        receipt['content_omitted'] = 'Older source view; full result is in the execution ledger. Read the needed current range before editing.'
        result[index] = {**messages[index], 'content': json.dumps(receipt, ensure_ascii=False)}
    committed = {}
    for message in messages:
        if message.get('role') != 'tool':
            continue
        try:
            value = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get('saved') and value.get('sha256') and not value.get('error'):
            committed[message.get('tool_call_id')] = value
    mutations = [(index, call) for index, message in enumerate(messages) for call in message.get('tool_calls', [])
                 if (call.get('function') or {}).get('name') in {'write_file', 'patch_file'} and call.get('id') in committed]
    for index, call in mutations[:-max(1, keep_mutations)]:
        function = call['function']
        try:
            arguments = json.loads(function['arguments'])
        except (ValueError, TypeError, KeyError):
            continue
        if not isinstance(arguments, dict):
            continue
        changed = False
        for key in ('content', 'old_text', 'new_text'):
            text = arguments.get(key)
            if isinstance(text, str) and len(text) > 600:
                sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
                del arguments[key]
                arguments.setdefault('historical_payload_omitted', {})[key] = {
                    'sha256': sha, 'characters': len(text),
                    'instruction': 'Older committed source omitted. Historical receipt only. Read current source before editing; no source content is present.'}
                changed = True
        if changed:
            projected = {**call, 'function': {**function, 'arguments': json.dumps(arguments, ensure_ascii=False)}}
            result[index] = {**result[index], 'tool_calls': [projected if c.get('id') == call['id'] else c for c in result[index]['tool_calls']]}
    validation_ids = {call.get('id') for message in messages for call in message.get('tool_calls', [])
                      if (call.get('function') or {}).get('name') == 'validate_project'}
    successful_validations = []
    for index, message in enumerate(messages):
        if message.get('role') != 'tool' or message.get('tool_call_id') not in validation_ids:
            continue
        try:
            value = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get('success') is True and isinstance(value.get('checks'), list):
            successful_validations.append((index, value))
    for index, value in successful_validations[:-1]:
        # The latest passing result and every failure stay intact. Host checks
        # always use the full durable report, never this historical projection.
        receipt = {key: value[key] for key in ('success', 'timestamp', 'fingerprint', 'validator_version',
                                              'source_unchanged_during_validation') if key in value}
        receipt['checks'] = [{key: check[key] for key in ('name', 'passed', 'tests', 'executed', 'tests_passed',
                                                        'validation_run_id', 'evidence') if key in check}
                             for check in value['checks'] if isinstance(check, dict)]
        receipt['historical_only'] = True
        receipt['note'] = 'Older passing validation; full output is in the execution ledger. Use the latest fresh validation and acceptance_plan for current exact IDs; source edits invalidate old evidence.'
        result[index] = {**messages[index], 'content': json.dumps(receipt, ensure_ascii=False)}
    # Only older inspection and explicitly identified passing test output is
    # projected. Raw ledger, failures, latest results and command data survive.
    functions = {call.get('id'): call.get('function') or {}
                 for message in messages for call in message.get('tool_calls', [])}
    groups = {'search_project': [], 'test_command': []}
    for index, message in enumerate(messages):
        if message.get('role') != 'tool':
            continue
        function = functions.get(message.get('tool_call_id'), {})
        name = function.get('name')
        try:
            value = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict) or value.get('error'):
            continue
        if name == 'search_project':
            groups['search_project'].append((index, value))
        elif name == 'run_command' and value.get('exit_code') == 0 and not value.get('timed_out'):
            argv = value.get('argv')
            if isinstance(argv, list) and is_test_command(argv):
                groups['test_command'].append((index, value))
    for group, rows in groups.items():
        for index, value in rows[:-1]:
            body = messages[index]['content']
            if len(body) <= 800:
                continue
            receipt = {key: value[key] for key in ('argv', 'exit_code', 'seconds', 'query', 'path', 'total') if key in value}
            receipt.update(historical_only=True, output_chars=len(body),
                           output_sha256=hashlib.sha256(body.encode()).hexdigest(),
                           note='Older '+group+' output; full result in execution ledger. Edits invalidate source snippets and test evidence; read or validate current state when needed.')
            result[index] = {**messages[index], 'content': json.dumps(receipt, ensure_ascii=False)}
    # Retain the latest mapping and catalog in full, while removing obsolete
    # copies of reviewed plans from the prompt only. Errors and raw ledger stay.
    acceptance_rows = []
    for index, message in enumerate(messages):
        function = functions.get(message.get('tool_call_id'), {})
        if message.get('role') != 'tool' or function.get('name') != 'acceptance_plan':
            continue
        try:
            value = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and not value.get('error'):
            acceptance_rows.append((index, value))
    for index, value in acceptance_rows[:-1]:
        if len(messages[index]['content']) < 800:
            continue
        receipt = {key: value[key] for key in ('saved', 'request_key', 'validation_current', 'missing_evidence', 'mapping_status') if key in value}
        receipt.update(historical_only=True, note='Earlier acceptance view. Use the latest plan/status; full result is retained in the execution ledger.')
        result[index] = {**result[index], 'content': json.dumps(receipt, ensure_ascii=False)}
    successful_saves = {messages[index].get('tool_call_id') for index, value in acceptance_rows if value.get('saved')}
    saves = [(index, call) for index, message in enumerate(messages) for call in message.get('tool_calls', []) if call.get('id') in successful_saves]
    for index, call in saves[:-1]:
        function = call.get('function') or {}
        try:
            args = json.loads(function.get('arguments') or '{}')
        except (ValueError, TypeError):
            continue
        if isinstance(args, dict) and isinstance(args.get('items'), list):
            args = {**args, 'items': [{'requirement_ids': row.get('requirement_ids', []), 'historical_only': True} for row in args['items'] if isinstance(row, dict)]}
            projected = {**call, 'function': {**function, 'arguments': json.dumps(args, ensure_ascii=False)}}
            result[index] = {**result[index], 'tool_calls': [projected if row.get('id') == call.get('id') else row for row in result[index]['tool_calls']]}
    # A file inventory is discovery evidence, not a source snapshot. Once later
    # tool evidence exists, stop retransmitting all paths on every inference.
    # The durable ledger and a fresh list_files call still expose the full list.
    tool_positions = [i for i, m in enumerate(messages) if m.get('role') == 'tool']
    latest_tool = tool_positions[-1] if tool_positions else -1
    for index, message in enumerate(messages):
        function = functions.get(message.get('tool_call_id'), {})
        if message.get('role') == 'tool' and function.get('name') == 'list_files' and index < latest_tool:
            try:
                value = json.loads(message.get('content') or '')
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict) or value.get('error') or not isinstance(value.get('files'), list):
                continue
            body = message['content']
            if len(body) > 1200:
                receipt = {'historical_only': True, 'root': value.get('root'),
                           'inventory_sha256': hashlib.sha256(body.encode()).hexdigest(),
                           'note': 'Earlier file inventory is retained in the execution ledger. Use list_files to refresh, or search_project for a specific path.'}
                result[index] = {**message, 'content': json.dumps(receipt, ensure_ascii=False)}
    # Older automatic reports remain verbatim except for their repeated file
    # sample. Every failure diagnostic, receipt and missing-file warning stays.
    automatic = [i for i, m in enumerate(messages) if m.get('role') == 'user'
                 and isinstance(m.get('content'), str)
                 and m['content'].startswith('Automatic validation after your edit: ')]
    prefix = 'Automatic validation after your edit: Current project files (sample): '
    for index in automatic[:-1]:
        body = result[index].get('content', '')
        if body.startswith(prefix) and '\n' in body:
            result[index] = {**result[index], 'content': 'Automatic validation after your edit: ' + body.split('\n', 1)[1]}
    return result


def is_test_command(argv):
    """Recognize runners, not shell fragments, application output or arbitrary modules."""
    if not argv or not all(isinstance(arg, str) for arg in argv):
        return False
    executable = argv[0].replace('\\', '/').rsplit('/', 1)[-1].casefold()
    if executable in {'python', 'python.exe', 'python3', 'python3.exe'}:
        return len(argv) >= 3 and argv[1:3] in (['-m', 'unittest'], ['-m', 'pytest'])
    if executable in {'node', 'node.exe'}:
        return '--test' in argv[1:] and not any(arg in {'-e', '--eval', '-p', '--print'} for arg in argv[1:])
    if executable in {'npm', 'npm.cmd'}:
        return argv[1:2] == ['test'] or argv[1:3] == ['run', 'test']
    return False


def diagnostic_excerpt(text, limit=2400):
    """Keep actual failure lines visible even when surrounded by huge TAP output."""
    import re
    if not isinstance(text, str) or len(text) <= limit:
        return text
    lines = text.splitlines()
    primary = [i for i, line in enumerate(lines) if re.search(r'TypeError|ReferenceError|AssertionError|SyntaxError|Traceback|error:|exception', line, re.I)]
    secondary = [i for i, line in enumerate(lines) if re.search(r'not ok|FAIL|failed|expected|actual', line, re.I)]
    selected, used = [], set()
    for index in primary + secondary:
        for at in range(max(0,index-1),min(len(lines),index+3)):
            if at not in used:
                selected.append(lines[at]); used.add(at)
        if len('\n'.join(selected)) >= limit:
            break
    return ('\n'.join(selected) if selected else text[-limit:])[:limit]


def tool_result_view(name, value):
    """Bound model-facing catalogs and retain failure diagnostics before metadata."""
    if name == 'list_files' and isinstance(value, dict) and isinstance(value.get('files'), list) and not value.get('error'):
        # All paths remain visible. Per-file byte sizes are available in the
        # durable result and read_file; avoid JSON object overhead per path.
        files = value['files']
        if all(isinstance(row, dict) and isinstance(row.get('path'), str) for row in files):
            return {**value, 'files': [row['path'] for row in files], 'total': len(files),
                    'note': 'Complete path inventory; per-file sizes retained in execution ledger.'}
    if name == 'run_command' and isinstance(value, dict) and value.get('exit_code') == 0 and is_test_command(value.get('argv') or []):
        result = dict(value)
        for key in ('stdout', 'stderr'):
            body = result.get(key)
            if isinstance(body, str) and len(body) > 1800:
                result[key] = body[-1200:]
                result[key + '_omitted'] = {'chars': len(body), 'sha256': hashlib.sha256(body.encode()).hexdigest(), 'note': 'Passing test command tail; full output in execution ledger. Acceptance uses validate_project receipts.'}
        return result
    if name in {'patch_file', 'write_file'} and isinstance(value, dict) and value.get('saved') and value.get('sha256') and not value.get('error'):
        return {**value, 'next_patch_expected_sha256': value['sha256'],
                'next_edit': 'For the next targeted patch use this SHA256 or @last_read (now tracks our committed bytes). Whole-file replacement still requires a complete current read; external edits are rejected.'}
    if name != 'validate_project' or not isinstance(value, dict) or not isinstance(value.get('checks'), list):
        return value
    checks, failures = [], []
    priority = value.get('new_successful_tests') or []
    for check in value['checks']:
        if not isinstance(check, dict):
            return value
        row = dict(check)
        identities = row.pop('test_identities', None)
        names = row.get('successful_tests')
        if isinstance(names, list) and len(names) > 24:
            ordered = list(dict.fromkeys([t for t in priority if t in names] + names))
            row['successful_tests'] = ordered[:24]
            row['successful_tests_catalog'] = {'total':len(names), 'shown':24, 'sample_only':True,
                'sha256':hashlib.sha256(json.dumps(names, ensure_ascii=False).encode()).hexdigest(),
                'lookup':'acceptance_plan(test_query=substring, test_offset=0, test_limit=64)'}
        omitted = {}
        for key in ('stdout', 'stderr'):
            body = row.get(key)
            if isinstance(body, str) and len(body) > 600 and (check.get('passed') is True or len(body) > 6000):
                omitted[key] = {'chars':len(body), 'sha256':hashlib.sha256(body.encode()).hexdigest()}
                if check.get('passed') is True:
                    row.pop(key)
                else:
                    row[key] = diagnostic_excerpt(body)
        if omitted:
            row['runner_output_omitted'] = omitted
        if check.get('passed') is not True:
            failures.append({'name':check.get('name'), 'error':check.get('error'),
                             'stdout':diagnostic_excerpt(check.get('stdout',''),1200),
                             'stderr':diagnostic_excerpt(check.get('stderr',''),1200)})
        checks.append(row)
    return {'failure_diagnostics':failures, **{k:v for k,v in value.items() if k != 'checks'},
            'checks':checks, 'full_report':'.local-ai/last_validation.json'}


def inspection_guidance(messages, tools, threshold=8):
    """Advisory for successful inspection loops; never disables source tools."""
    if not any(t['function']['name'] in {'patch_file', 'write_file'} for t in tools):
        return None
    calls = {call.get('id'): (call.get('function') or {}).get('name')
             for message in messages for call in message.get('tool_calls', [])}
    details = {call.get('id'): call.get('function') or {}
               for message in messages for call in message.get('tool_calls', [])}
    catalog_reads = 0
    for message in reversed(messages):
        if message.get('role') != 'tool':
            continue
        info = details.get(message.get('tool_call_id'), {})
        if info.get('name') != 'acceptance_plan':
            break
        try:
            arguments = json.loads(info.get('arguments') or '{}')
        except (ValueError, TypeError):
            break
        if not isinstance(arguments, dict) or 'items' in arguments or arguments.get('repair'):
            break
        catalog_reads += 1
    if catalog_reads >= 3:
        return ('Acceptance checkpoint: stop querying individual test names already shown. '
                'Use the current repair_proposal and call acceptance_plan(repair=true, plan_revision=...) for link corrections; include explicit repairs for reviewed behavioral IDs. Preserve every criterion and valid link. If the plan is missing, save the behavior mappings once with items. '
                'Add and validate a focused test only for an actual uncovered behavior. '
                'Do not repeat the entire review or original suite for a missing mapping. '
                'All host evidence and freshness requirements still apply.')
    browser_changes = 0
    for message in reversed(messages):
        if message.get('role') != 'tool':
            continue
        info = details.get(message.get('tool_call_id'), {})
        if info.get('name') != 'browser_project':
            break
        try:
            arguments = json.loads(info.get('arguments') or '{}')
            result = json.loads(message.get('content') or '{}')
        except (ValueError, TypeError):
            break
        if not isinstance(result, dict) or result.get('error') or result.get('not_executed'):
            break
        action = arguments.get('action') if isinstance(arguments, dict) else None
        if action == 'assert':
            break
        if action in {'fill', 'press', 'click', 'select', 'open', 'reload', 'close'}:
            browser_changes += 1
        elif action not in {'snapshot', 'screenshot', 'viewport'}:
            break
    if browser_changes >= threshold:
        return (f'Browser evidence checkpoint: {browser_changes} interactions without a recent assertion. '
                'Follow the specified scenario, complete its prerequisite actions and capture its required assertions. '
                'Reopening pages and reading snapshots are not assertion evidence. '
                'Avoid repeating input changes or key presses without a new test question. '
                'Once the required evidence passes, finish remaining requirements and deliver. '
                'This advice does not disable tools or authorize new actions.')
    inspection = {'read_file', 'search_project', 'list_files', 'lookup_docs',
                  'read_project_requirements', 'read_continuation'}
    count, source_seen = 0, False
    for message in reversed(messages):
        if message.get('role') != 'tool':
            continue
        name = calls.get(message.get('tool_call_id'))
        if name not in inspection:
            break
        try:
            result = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            break
        if not isinstance(result, dict) or result.get('error') or result.get('not_executed'):
            break
        count += 1
        source_seen |= name == 'read_file' and bool(result.get('sha256'))
    if count < threshold or not source_seen:
        return None
    return (f'Inspection checkpoint: {count} read/search calls without a change, test or reproduction. '
            'Use the inspected evidence to complete the ORIGINAL request. For an authorized repair, '
            'execute one minimal targeted patch or a focused reproduction next, then validate. '
            'Read additional code only to resolve a specific remaining question; avoid unrelated helpers. '
            'Keep all requested behavior and existing tests. This checkpoint grants no new authorization.')
