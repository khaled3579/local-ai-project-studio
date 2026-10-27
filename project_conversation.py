"""Durable user-facing conversation, separate from the model/tool protocol."""
import hashlib
import json


def assistant_entry(job_id, message, *, kind='progress', generation_id=None):
    content = message.get('content')
    content = content if isinstance(content, str) else ''
    calls = message.get('tool_calls') or []
    names = [call.get('function', {}).get('name') for call in calls if isinstance(call, dict)]
    names = [name for name in names if isinstance(name, str) and name]
    if not content.strip() and names:
        content = 'الأدوات: ' + '، '.join(names)
    if not content.strip():
        return None
    identity = json.dumps([job_id, content, [call.get('id') for call in calls if isinstance(call, dict)]], ensure_ascii=False)
    key = 'assistant:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
    if kind == 'partial' and generation_id:
        key = 'partial:' + str(job_id) + ':' + generation_id
    if kind == 'reasoning' and generation_id:
        key = 'reasoning:' + str(job_id) + ':' + generation_id
    return {'id': key, 'job_id': job_id, 'role': 'assistant', 'content': content,
            'tool_names': names,
            'verified': False, 'kind': kind, 'transcript_only': True,
            **({'generation_id': generation_id} if generation_id else {})}


def execution_receipt(state):
    """A labelled display receipt from saved results, never from hidden reasoning."""
    job = state.get('job') or {}
    if job.get('status') != 'complete' or not job.get('id') or job.get('mode') == 'chat':
        return None
    messages = (state.get('agent_state') or {}).get('messages') or []
    last_answer = next((message for message in reversed(messages) if message.get('role') == 'assistant'), None)
    if last_answer is None or last_answer.get('tool_calls') or str(last_answer.get('content') or '').strip():
        return None
    calls = {call.get('id'): call.get('function') or {} for message in messages
             for call in message.get('tool_calls') or [] if isinstance(call, dict)}
    report = None
    changes = set()
    diagnostic_errors = 0
    for message in messages:
        if message.get('role') != 'tool':
            continue
        function = calls.get(message.get('tool_call_id')) or {}
        try:
            result = json.loads(message.get('content') or '{}')
            arguments = json.loads(function.get('arguments') or '{}')
        except (ValueError, TypeError):
            continue
        if not isinstance(result, dict) or not isinstance(arguments, dict):
            continue
        if function.get('name') == 'diagnose_project' and result.get('error'):
            diagnostic_errors += 1
        if result.get('error') or result.get('not_executed'):
            continue
        if function.get('name') == 'validate_project' and type(result.get('success')) is bool:
            report = result
        if function.get('name') in {'write_file', 'patch_file'} and isinstance(arguments.get('path'), str):
            changes.add(arguments['path'])
    lines = ['**ملخص التنفيذ من السجل المحفوظ**',
             'لم يرسل النموذج إجابة نهائية نصية لهذه المهمة؛ يعرض هذا الملخص إيصالات التنفيذ المحفوظة.']
    if report is not None:
        lines.append('التحقق وقت التنفيذ: ' + ('نجح.' if report.get('success') else 'لم ينجح.'))
        for check in report.get('checks') or []:
            if check.get('name') == 'python_syntax':
                lines.append('فحص صياغة Python: ' + str(check.get('files', 'غير متاح')) + ' ملفًا؛ النتيجة: ' + ('ناجح.' if check.get('passed') else 'تحتاج متابعة.'))
            if check.get('tests_passed') is not None:
                lines.append('اختبارات ' + str(check.get('name', '')) + ': نجح ' + str(check['tests_passed']) + ' من ' + str(check.get('executed', check.get('tests', 'غير متاح'))) + ' وقت التنفيذ.')
    else:
        lines.append('تفاصيل الفحص غير متاحة في هذا المقتطف؛ راجع تقرير المهمة المحفوظ.')
    if changes:
        lines.append('ملفات لها إيصالات تعديل في هذه المهمة: ' + '، '.join(sorted(changes)))
    elif not any(function.get('name') in {'write_file', 'patch_file'} for function in calls.values()):
        lines.append('الإصلاحات: لم تُسجّل استدعاءات تعديل ملفات في هذه المهمة؛ كان العمل فحصًا فقط.')
    else:
        lines.append('لا يوجد في هذا المقتطف إيصال ناجح يؤكد تعديل الملفات.')
    if diagnostic_errors:
        lines.append('طلبات تشخيص تعذرت سابقًا: ' + str(diagnostic_errors) + '؛ أخطاؤها باقية في سجل العمليات.')
    lines.append('النتائج تخص الفحوص المسجّلة، ولا تثبت خلو المشروع من جميع الأخطاء المنطقية أو نجاح الاستخدام الفعلي.')
    return {'id': 'execution-receipt:' + job['id'], 'job_id': job['id'], 'role': 'assistant',
            'kind': 'execution_receipt', 'transcript_only': True, 'verified': False,
            'content': '\n\n'.join(lines)}


def conversation_view(sessions, session_id, checkpoints):
    """Join explicit continuation parents and recover only actual assistant turns.

    This is a read-only display projection: internal prompts stay private.
    A labelled host receipt covers legacy completed tasks with empty answers;
    it never promotes reasoning text or changes stored conversation messages.
    """
    by_id = {session['id']: session for session in sessions}
    chain, seen = [], set()
    current = by_id.get(session_id)
    while current and current['id'] not in seen:
        chain.append(current)
        seen.add(current['id'])
        current = by_id.get(current.get('parent_id')) if current.get('continuation_number') else None
    result = []
    for session in reversed(chain):
        rows = list(session.get('messages', []))
        # Identify legacy handoffs only in explicit continuation sessions.
        # Copy the display row without changing durable messages or model context.
        if session.get('parent_id') and session.get('continuation_number') and rows:
            first = rows[0]
            text = first.get('content')
            if (first.get('role') == 'user' and isinstance(text, str)
                    and not any(key in first for key in ('mode', 'draft_edit_id'))
                    and text.startswith(('متابعة تلقائية لتغيير خطة الإصلاح. الطلب الأصلي: ',
                                         'متابعة تلقائية بسبب امتلاء السياق. الطلب الأصلي: '))):
                rows[0] = {**first, 'kind': 'continuation'}
        known_ids = {row.get('id') for row in rows if row.get('id')}
        known_text = {row.get('content') for row in rows if row.get('role') == 'assistant' and isinstance(row.get('content'), str)}
        for state in reversed(checkpoints):
            if state.get('session_id') != session['id']:
                continue
            agent = state.get('agent_state') or {}
            messages = agent.get('messages') or []
            prompt = agent.get('model_prompt')
            starts = [index for index, message in enumerate(messages)
                      if message.get('role') == 'user' and prompt is not None and message.get('content') == prompt]
            if not starts:
                continue
            recovered = []
            for message in messages[starts[-1] + 1:]:
                if message.get('role') != 'assistant':
                    continue
                entry = assistant_entry((state.get('job') or {}).get('id'), message, kind='recovered')
                if entry and entry['id'] not in known_ids and entry['content'] not in known_text:
                    recovered.append(entry)
                    known_ids.add(entry['id']); known_text.add(entry['content'])
            # Legacy final answers stay after recovered progress for that turn.
            job = state.get('job') or {}
            anchors = [i for i, row in enumerate(rows) if row.get('role') == 'user' and row.get('content') == job.get('prompt')]
            insert_at = len(rows)
            if anchors:
                insert_at = next((i for i in range(anchors[-1] + 1, len(rows))
                                  if rows[i].get('role') == 'assistant' and not rows[i].get('transcript_only')), len(rows))
            rows[insert_at:insert_at] = recovered
        for state in checkpoints:
            if state.get('session_id') != session['id']:
                continue
            job_id = (state.get('job') or {}).get('id')
            if any(row.get('job_id') == job_id and row.get('kind') in {'final', 'execution_receipt'} and str(row.get('content') or '').strip() for row in rows):
                continue
            receipt = execution_receipt(state)
            if receipt is not None:
                rows.append(receipt)
        result.extend({**row, 'source_session_id': session['id']} for row in rows)
    return result
