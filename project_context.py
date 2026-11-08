"""Lossless execution ledger, bounded model-facing projection of old reads."""
import json
import hashlib


class RequestContextOverflow(ValueError):
    """The fully assembled request needs a durable history rollover first."""
    def __init__(self, history_budget, input_tokens, context):
        super().__init__('لا يتسع الطلب الفعلي مع مساحة الإخراج؛ يلزم تقليص سجل المتابعة مع احتساب تعليمات المرحلة')
        self.history_budget = history_budget
        self.input_tokens = input_tokens
        self.context = context


def overflow_history_budget(context, body, prefix_size, count, output_tokens=4096):
    """Reserve actual host suffix/schema overhead before choosing handoff size.

    The durable ledger is unchanged. count includes the model's real template;
    both measurements use the SAME current tool schema and valid user turn.
    """
    prefix = [body['messages'][0], {'role': 'user', 'content': 'Continue the current task.'}]
    tools = body.get('tools', [])
    base = count(prefix, tools)
    with_suffix = count(prefix + body['messages'][prefix_size:], tools)
    overhead = max(0, with_suffix - base)
    reserve = min(output_tokens, max(256, (context - with_suffix - 512) // 2))
    budget = context - overhead - reserve - 512
    if budget < base + 512:
        raise ValueError('تعليمات المرحلة وفهرس الأدوات لا يتركان مساحة للمتابعة؛ ارفع سياق النموذج دون حذف المتطلبات')
    return budget


def source_window(context):
    """Use available large-context capacity for a bounded source working set.

    The controller must still measure the COMPLETE request and fall back to the
    default projection before spending any output or format-recovery budget.
    """
    if type(context) is not int:
        return {}
    if context >= 131072:
        return {'keep_reads': 12, 'read_chars': 96000}
    if context >= 65536:
        return {'keep_reads': 8, 'read_chars': 64000}
    return {}


def deduplicate_reads(messages):
    """Remove only older complete single-call read pairs from the prompt copy.

    Exact typed selectors and a successful write barrier scope each source view.
    Errors, batches, assistant notes, and the durable input remain untouched.
    """
    epochs, candidates = {}, []
    ids, replies = {}, {}
    for message in messages:
        if message.get('role') == 'tool':
            reply_id = message.get('tool_call_id')
            replies[reply_id] = replies.get(reply_id, 0) + 1
        for call in message.get('tool_calls', []):
            ids[call.get('id')] = ids.get(call.get('id'), 0) + 1
    for index, message in enumerate(messages):
        if message.get('role') != 'tool':
            continue
        try:
            value = json.loads(message.get('content') or '')
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        if isinstance(value.get('saved'), str) and not value.get('error') and not value.get('not_executed'):
            epochs[value['saved']] = epochs.get(value['saved'], 0) + 1
        if not index or value.get('error') or value.get('not_executed'):
            continue
        previous = messages[index-1]
        calls = previous.get('tool_calls') or []
        if (previous.get('role') != 'assistant' or previous.get('content') or len(calls) != 1
                or calls[0].get('id') != message.get('tool_call_id')
                or ids.get(message.get('tool_call_id')) != 1 or replies.get(message.get('tool_call_id')) != 1):
            continue
        function = calls[0].get('function') or {}
        if function.get('name') != 'read_file':
            continue
        try:
            arguments = json.loads(function.get('arguments') or '{}')
        except (ValueError, TypeError):
            continue
        if (not isinstance(arguments, dict) or not isinstance(value.get('content'), str)
                or not isinstance(value.get('path'), str) or not value['path']
                or not isinstance(value.get('sha256'), str) or not value['sha256']):
            continue
        identity = (json.dumps(arguments, sort_keys=True, ensure_ascii=False), value['path'], value['sha256'],
                    epochs.get(value['path'], 0), *(value.get(key) for key in
                    ('start_line', 'end_line', 'offset', 'char_start', 'char_end')))
        # Results are untrusted data: malformed compound view fields must not
        # crash context construction or authorize removal of any history.
        try:
            hash(identity)
        except TypeError:
            continue
        candidates.append((index, identity))
    seen, omitted = set(), set()
    for index, identity in reversed(candidates):
        if identity in seen:
            omitted.update((index-1, index))
        seen.add(identity)
    return [message for index, message in enumerate(messages) if index not in omitted]


def project_messages(messages, keep_reads=4, read_chars=24000, keep_mutations=2):
    """Only omit older source bodies; never mutate durable messages or tool pairs."""
    messages = deduplicate_reads(messages)
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
    if (name == 'read_file' and isinstance(value, dict) and value.get('reused_read')
            and not value.get('error') and not value.get('not_executed')):
        view = {key: value[key] for key in ('path', 'sha256', 'start_line', 'end_line', 'offset',
                                          'char_start', 'char_end', 'more') if key in value}
        next_read = value.get('next_read')
        next_call = ({'tool': 'read_file', 'arguments': dict(next_read)}
                     if value.get('more') is True and isinstance(next_read, dict) else None)
        return {**value, 'read_recovery': {'status': 'already_read_on_unchanged_source', 'view': view,
            'next_call': next_call,
            'instruction': 'This exact source view was already returned; do not repeat the same read. '
                + ('If omitted source is needed, use next_call exactly, or select a different relevant symbol/line range. '
                   if next_call else 'The selected view is complete. Use the returned source for the authorized next step. '
                   'Read a different symbol/line range only for a specific unresolved question. ')
                + 'No page was advanced, edit executed, or acceptance granted by this advice.'}}
    if name == 'review_project_requirements' and isinstance(value, dict) and value.get('saved'):
        batch = value.get('next_source_batch')
        batch = json.loads(json.dumps(batch)) if isinstance(batch, dict) else {}
        pending = batch.get('pending_source_reviews', value.get('pending_source_reviews',
            len(value.get('pending_obligation_reviews', []))))
        requirements = value.get('requirements', [])
        issues = [row for row in value.get('review_issues', [])
                  if not row.startswith('Source obligations need explicit span/evidence review:')]
        view = {'saved': True, 'catalog_revision': value.get('catalog_revision'),
                'reviewed_blocks': value.get('reviewed_blocks', []),
                'pending_source_reviews': pending, 'next_source_batch': batch,
                'requirement_count': len(requirements),
                'requirement_ids': [row['id'] for row in requirements[:128]] if not pending else [],
                'requirement_ids_truncated': not pending and len(requirements)>128,
                'review_issues': [row[:300] for row in issues[:3]], 'review_issue_count': len(issues),
                'next_tool': 'review_project_requirements' if pending else 'project_plan',
                'next_action': 'Use the current catalog_revision and next_source_batch. Do not re-review saved blocks. '
                    'After source review, save project_plan covering every current requirement ID; read '
                    'read_project_requirements pages if IDs or original text are omitted. No code completion is inferred.'}
        if value.get('executed_evidence_hint'):
            view['executed_evidence_hint'] = value['executed_evidence_hint'][:1600]
        while len(json.dumps(view, ensure_ascii=False)) > 10500:
            if len(batch.get('source_blocks', [])) > 1:
                batch['source_blocks'].pop()
                batch['shown_source_blocks'] = len(batch['source_blocks'])
            elif view.get('executed_evidence_hint'):
                view.pop('executed_evidence_hint')
            elif view['requirement_ids']:
                view['requirement_ids'].pop()
                view['requirement_ids_truncated'] = True
            else:
                break
        return view
    if (name == 'project_plan' and isinstance(value, dict) and 'stages' in value
            and len(json.dumps(value, ensure_ascii=False)) > 10500):
        stages = value['stages']
        selected = stages[:10]
        active = next((stage for stage in stages if stage.get('status') == 'active'), None)
        if active and active not in selected:
            selected[-1:] = [active]
        return {key: value.get(key) for key in ('revision', 'path', 'plan_required', 'needs_review',
                'ready_for_delivery', 'next_call')} | {
            'truncated': True, 'stage_count': len(stages), 'stages_shown': len(selected),
            'active_stage_ids': [stage['id'] for stage in stages if stage.get('status') == 'active'],
            'stage_summaries': [{'id': stage['id'], 'title': stage['title'][:160], 'status': stage['status'],
                'depends_on': stage.get('depends_on', [])[:6], 'dependency_count': len(stage.get('depends_on', [])),
                'requirement_count': len(stage.get('requirement_ids', [])),
                'criterion_count': len(stage.get('criteria', []))} for stage in selected],
            'scope_change_count': len(value.get('scope_changes', [])),
            'open_question_count': len(value.get('open_questions', [])),
            'open_questions': [question[:400] for question in value.get('open_questions', [])[:3]],
            'open_questions_truncated': len(value.get('open_questions', []))>3 or
                any(len(question)>400 for question in value.get('open_questions', [])),
            'next_action': 'The full plan and every criterion remain in .local-ai/PROJECT_PLAN.md. '
                'Read it with read_file using offset/max_chars pages. Use this revision for plan mutations; '
                'never infer missing stage details from a clipped JSON fragment.'}
    if name == 'acceptance_plan' and isinstance(value, dict) and value.get('routed_to') == 'link_acceptance_evidence':
        return value
    if name=='review_project_requirements' and isinstance(value,dict) and value.get('review_repair'):
        return {'error':str(value.get('error',''))[:1200],'review_repair':value['review_repair'],
                'review_attempt_limit':value.get('review_attempt_limit'), 'progress_hint':value.get('progress_hint')}
    if name=='acceptance_plan' and isinstance(value,dict) and value.get('source_review'):
        plan=value.get('plan') or {}
        review={**value['source_review'],'blocks':[dict(r) for r in value['source_review']['blocks']]}
        host={key for row in review['blocks'] for key in row.get('supported_host_evidence',[])}
        view={'catalog_revision':value.get('catalog_revision'),'source_review':review,
            'plan_revision':(value.get('repair_proposal') or {}).get('plan_revision') if value.get('validation_current') and plan else None,
            'validation_current':value.get('validation_current'),'successful_tests':value.get('successful_tests',[])[:8],
            'available_evidence':{k:{'passed':bool(v.get('passed'))} for k,v in value.get('available_evidence',{}).items() if k in host},
            'existing_plan_retained':bool(plan),'plan_items':len(plan.get('items',[])),
            'next_action':'Repair source review metadata first using the exact source_review block texts and catalog_revision. Preserve the current plan, code and passing validation. Read acceptance_plan again after review for full criteria and link repair; no requirement is removed by this bounded view.'}
        while len(json.dumps(view,ensure_ascii=False))>10500 and len(review['blocks'])>1:
            review['blocks'].pop()
        review['shown']=len(review['blocks'])
        return view
    if name == 'acceptance_plan' and isinstance(value, dict):
        status = value.get('mapping_status') or {}
        missing = status.get('unmapped_requirements') or []
        proposal = value.get('repair_proposal') or {}
        ticket = value.get('plan_revision') or (
            proposal.get('plan_revision') if proposal.get('validation_current') and not proposal.get('missing_plan') else None)
        if missing and (len(missing) > 8 or value.get('saved') or len(value.get('requirements', [])) > 40):
            plan = value.get('plan') or {}
            return {
                'saved': bool(value.get('saved')), 'catalog_revision': value.get('catalog_revision'),
                'plan_revision': ticket, 'revision_refreshed': bool(value.get('revision_refreshed')),
                'unmapped_count': len(missing), 'validation_current': value.get('validation_current'),
                'batch_diagnostics': value.get('batch_diagnostics', []),
                'mapped_rows_needing_repair': [
                    {'item_id': row['item_id'], 'missing_tests': row['missing_tests'][:2],
                     'candidate_successful_tests': row['candidate_successful_tests'][:2]}
                    for row in status.get('defects', [])[:2]],
                'unmapped_batch': [{'id': row['id'], 'text': row['text'][:600],
                                    'text_truncated': len(row['text']) > 600}
                                   for row in missing[:8]],
                'successful_tests': value.get('successful_tests', [])[:16],
                'test_catalog': value.get('test_catalog'),
                'next_tool': value.get('next_tool'),
                'next_action': (value.get('next_action') + ' ' if value.get('link_only_repair') else '') +
                               'Review up to eight requirements against relevant executed assertions, then save only those mappings with complete=false. '
                               + ('Use merge=true to retain existing rows. ' if plan or value.get('saved') else '')
                               + 'Read full source text if truncated. If no relevant evidence exists, add a behavioral test and validate. Drafts cannot authorize delivery.',
            }
        if ticket and len(json.dumps(value, ensure_ascii=False)) > 10000:
            return {'plan_revision': ticket, 'validation_current': value.get('validation_current'),
                    'saved': bool(value.get('saved')), 'unchanged': value.get('unchanged'),
                    'revision_refreshed': bool(value.get('revision_refreshed')),
                    'ready_for_final_response': status.get('ready_for_final_response', False),
                    'next_tool': value.get('next_tool'),
                    'mapped_rows_needing_repair': [
                        {'item_id': row['item_id'], 'missing_tests': row['missing_tests'][:3],
                         'candidate_successful_tests': row['candidate_successful_tests'][:3]}
                        for row in status.get('defects', [])[:4]],
                    'next_action': value.get('next_action') or proposal.get('action')}
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
        return {**value, 'path': value['saved'], 'next_patch_expected_sha256': '@last_read',
                'next_edit': 'Use this exact workspace-relative path and @last_read for the next targeted patch. '
                    'The host supplies our committed receipt. Read the current relevant range when needed. '
                    'Whole-file replacement still requires a complete current read; external edits are rejected.'}
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
                             'stderr':diagnostic_excerpt(check.get('stderr',''),1200),
                             **{key:check[key] for key in ('changed_files', 'changed_file_count',
                                 'changed_files_truncated', 'next_action') if key in check}})
        checks.append(row)
    return {'failure_diagnostics':failures, **{k:v for k,v in value.items() if k != 'checks'},
            'checks':checks, 'full_report':'.local-ai/last_validation.json'}


def inspection_guidance(messages, tools, threshold=8):
    """Advisory for successful inspection loops; never disables source tools."""
    if not any(t['function']['name'] in {'patch_file', 'write_file'} for t in tools):
        return None
    browser_state=next((t['function']['parameters'].get('title') for t in tools if t['function']['name']=='browser_project'),None)
    if browser_state=='Browser preview missing':
        return ('Browser preparation checkpoint: no live preview exists. Historical open/upload results are not current state. '
                'Use the offered browser_project snapshot with an explicit HTML entry; this prepares only and replays no interaction/assertion. '
                'Then inspect the fresh page and execute the preserved scenario explicitly. No acceptance is granted.')
    if browser_state=='Browser preview source changed':
        return ('Browser preparation checkpoint: loaded source is stale. Use the offered reload or explicit open before any interaction/assertion. '
                'Preparation never replays completed actions or grants evidence; continue the actual required scenario afterwards.')
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
                'Use link_acceptance_evidence for link corrections; copy its current row IDs and include explicit repairs for reviewed behavioral IDs. Preserve every criterion and valid link. If the plan is missing, save the behavior mappings once with items. '
                'Add and validate a focused test only for an actual uncovered behavior. '
                'Do not repeat the entire review or original suite for a missing mapping. '
                'All host evidence and freshness requirements still apply.')
    browser_changes = 0
    preview_ready = False
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
            detail=result.get('result',result)
            assertion=detail.get('assertion') if isinstance(detail,dict) else None
            if browser_state=='Browser preview ready' and isinstance(assertion,dict) and assertion.get('passed') is True:
                if assertion.get('source') in {'visible','enabled'}:
                    return ('Browser assertion checkpoint: this proves structure/visibility only. If the host still requires interaction, CSV content, saved-data or format proof, '
                            'perform the required scenario and assert actual changed text/value/checked/count or decoded output, with expected chosen from the original criterion. '
                            'Do not repeat snapshots/open or count visibility as successful import/persistence. Preserve criteria and links; no new acceptance was granted.')
                return ('Browser assertion checkpoint: this exact stated outcome passed; the latest DOM/selector_catalog is already returned. '
                        'Follow the host remaining_observables/current stage: collect any other required outcome, then link existing criteria with link_acceptance_evidence or map genuinely missing criteria when offered. '
                        'Do not repeat snapshots or the same assertion on unchanged state. A passing assertion is not whole-project completion and does not bypass acceptance gates.')
            break
        detail = result.get('result',result)
        if action in {'open','reload'} and isinstance(detail,dict) and browser_changes == 0:
            preview_ready = True
        if action == 'upload' and isinstance(detail,dict) and (detail.get('upload_receipt') or {}).get('selected_files'):
            files=detail['upload_receipt']['selected_files']
            if detail.get('input_kind')=='csv' or any(str(f.get('name','')).lower().endswith('.csv') or f.get('mime')=='text/csv' for f in files if isinstance(f,dict)):
                return ('CSV upload checkpoint: the INPUT is already selected. Inspect actual import status/rows using the current selector_catalog and assert the required content. '
                        'Export and persistence require their own actual proof. Do not repeat upload or look for converter images for a CSV scenario. No acceptance was granted.')
            return ('Browser upload checkpoint: the latest successful operation selected the INPUT file. '
                    'Do not repeat selecting it or click the file input as a substitute for upload. '
                    'Finish the specified scenario prerequisites, then assert its actual output/state. '
                    'Inspect output_images and the actual converter script if no output IMG is exposed. '
                    'For conversion use image_format on the real converted output; input selection/visibility is not WebP or persistence evidence. '
                    'If the browser session was reopened, inspect its fresh state before proceeding. No acceptance was granted.')
        if action in {'fill', 'press', 'click', 'select', 'upload', 'open', 'reload', 'close'}:
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
    if preview_ready and browser_state=='Browser preview ready':
        return ('Browser preview checkpoint: the requested page is already open on its loaded source. '
                'Use the returned snapshot/selectors to perform the preserved current criterion scenario and then assert an explicit expected result. '
                'Do not reopen the same page to collect evidence; opening, reloading and snapshots do not prove the criterion. '
                'Use open with a different entry only when switching pages, or reload for a deliberate reset. '
                'If source changes, explicitly reload/open before assertions. No action is replayed and no acceptance is granted.')
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


def stale_preview_guard(workspace, state, arguments):
    """Reject actions on an older loaded source; never reload or replay implicitly."""
    if arguments.get('action') not in {'assert','fill','click','select','press','upload'}:
        return None
    loaded = state.get('browser_loaded_fingerprint')
    if not loaded or loaded == workspace.fingerprint(force=True):
        return None
    return {'not_executed': True, 'stale_browser_preview': True,
            'error': 'The loaded browser page predates the current project source. No interaction or assertion was executed.',
            'next_tool': 'browser_project',
            'next_action': 'Reload the current preview with action=reload, or explicitly open the intended HTML entrypoint if changing pages. Then complete the scenario prerequisites again and assert actual output on this current source. Do not repeat the rejected action on the old page. Existing receipts remain historical; no acceptance or fresh evidence was granted.'}


def application_lifecycle_tools(tools, workspace):
    """An already stopped application has no stop action; never add disabled tools."""
    process = getattr(workspace,'running',None)
    running = process is not None and process.poll() is None
    return [tool for tool in tools if tool['function']['name'] != 'stop_project' or running]
