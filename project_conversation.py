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


def conversation_view(sessions, session_id, checkpoints):
    """Join explicit continuation parents and recover only actual assistant turns.

    This is a read-only display projection: no internal user/system prompts,
    tool arguments/results, synthetic messages or execution receipts are added.
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
        result.extend({**row, 'source_session_id': session['id']} for row in rows)
    return result
