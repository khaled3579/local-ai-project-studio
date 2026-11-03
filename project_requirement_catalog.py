"""Project-wide acceptance sources; attachment contents remain untrusted data."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from app_maintenance import atomic_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plain(text):
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w\s]', ' ', normalized).split())


def request_kind(text):
    normalized = plain(text)
    if normalized in {plain(x) for x in ('اكمل', 'اكمل من حيث توقفت', 'اكمل العمل', 'استئناف السلسلة', 'استكمال السلسلة',
                       'تابع', 'متابعة', 'continue', 'resume', 'resume task', 'continue where you left off')}:
        return 'continuation'
    if normalized.startswith(('قدم اقتراحات', 'اقترح تحسينات', 'ما رايك', 'ماهي اقتراحات', 'suggest improvements')):
        return 'advisory'
    return 'requirement'


def clauses(text):
    # Exact source blocks, not claims of automatic semantic decomposition.
    rows = []
    for line in re.split(r'(?<=[.!?;؛؟])\s+|\n+', text):
        line = line.strip()
        if not line or re.fullmatch(r'[#*\-_=|`\s]+', line):
            continue
        while len(line) > 1800:
            cut = line.rfind(' ', 0, 1800)
            if cut < 1: cut = 1800
            rows.append(line[:cut]); line = line[cut:].lstrip()
        if line: rows.append(line)
    return rows


def safe_path(workspace, name):
    path = workspace.meta / name
    if path.is_symlink() or not path.resolve().is_relative_to(workspace.meta.resolve()):
        raise ValueError('Invalid requirements catalog path')
    return path


def build(workspace, value, sessions=None, *, persist=True):
    path = safe_path(workspace, 'requirements-catalog.json')
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if previous and (previous.get('version') != 1 or not isinstance(previous.get('sources'), list)):
        raise ValueError('Invalid requirements catalog; existing data preserved')
    refs = dict(previous.get('request_attachments', {})); sources = []; issues = []
    old_sources = {row['id']:row for row in previous.get('sources', [])}
    history = None
    for request in value['requests']:
        from project_requirement_lifecycle import classify
        if classify(request['text']) not in {'add','amend'}:
            continue
        sid = 'request:' + request['id']
        if sid in old_sources and old_sources[sid]['text'] != request['text']:
            issues.append(sid + ': recorded original request changed; review required')
        sources.append({'id':sid, 'kind':'user_request', 'text':request['text'],
                        'request_id':request['id'], 'session_id':request.get('session_id'),
                        'recorded_at':request.get('time'), 'request_kind':classify(request['text'])})
        if request['id'] not in refs:
            if 'attachments' in request:
                refs[request['id']] = request['attachments']
            else:
                if history is None:
                    history = sessions
                    if history is None:
                        stored = safe_path(workspace, 'sessions.json')
                        history = json.loads(stored.read_text(encoding='utf-8')).get('sessions', []) if stored.exists() else []
                refs[request['id']] = [a for session in history if session['id'] == request.get('session_id')
                    for message in session.get('messages', []) if message.get('role') == 'user'
                    and message.get('content') == request['text'] for a in message.get('attachments', [])]
        # A referenced specification is evidence of requirements, never authority
        # to execute instructions/scripts or override the actual user's request.
        authorized = re.search(r'مرفق|المواصفات|التعليمات|وفق|حسب|attached|specification|requirements|\bprd\b|\bplan', request['text'], re.I)
        if not authorized:
            continue
        for ref in refs[request['id']]:
            if ref.get('kind') != 'text' or Path(ref.get('name', '')).suffix.lower() not in {'.md','.txt','.pdf','.docx'}:
                continue
            attachment_id = ref.get('id', '')
            if not re.fullmatch('[a-f0-9]{32}', attachment_id):
                issues.append('Invalid specification attachment identifier'); continue
            source_id = 'attachment:' + attachment_id
            if any(s['id'] == source_id for s in sources):
                continue
            try:
                directory = safe_path(workspace, 'attachments/' + attachment_id)
                metadata = json.loads(safe_path(workspace, 'attachments/' + attachment_id + '/metadata.json').read_text(encoding='utf-8'))
                raw = safe_path(workspace, 'attachments/' + attachment_id + '/original').read_bytes()
                if hashlib.sha256(raw).hexdigest() != ref.get('sha256') or metadata.get('sha256') != ref.get('sha256'):
                    raise ValueError('Specification attachment hash mismatch')
                text = safe_path(workspace, 'attachments/' + attachment_id + '/text.txt').read_text(encoding='utf-8')
                row = {'id':source_id, 'kind':'referenced_specification', 'text':text,
                       'request_id':request['id'], 'attachment_id':attachment_id,
                       'recorded_at':request.get('time'),
                       'name':ref['name'], 'original_sha256':ref['sha256'], 'untrusted_source':True}
                if source_id in old_sources and old_sources[source_id]['text'] != text:
                    raise ValueError('Pinned specification text changed; review required')
                sources.append(row)
            except (OSError, ValueError, KeyError) as error:
                issues.append(source_id + ': ' + str(error))
                if source_id in old_sources: sources.append(old_sources[source_id])
    if value.get('brief', '').strip():
        sources.append({'id':'brief', 'kind':'user_brief', 'text':value['brief'],
                        'recorded_at':value.get('brief_updated_at'),
                        'previous_revision':digest(value['revisions'][-1]['brief']) if value.get('revisions') else None,
                        'revision':digest(value['brief'])})
    active = {s['id'] for s in sources}
    for sid, row in old_sources.items():
        if sid not in active and row['kind'] != 'user_brief':
            issues.append(sid + ': source disappeared; explicit review required')
            sources.append(row)
    requirements = []
    for source in sources:
        seen = {}
        for text in clauses(source['text']):
            key = digest([source['id'], text]); seen[key] = seen.get(key, 0) + 1
            requirements.append({'id':'R_' + key[:24] + '_' + str(seen[key]), 'text':text,
                                 'source_id':source['id'], 'source_kind':source['kind']})
    if not requirements:
        issues.append('No original requirements available; a continuation alone is not a specification')
    result = {'version':1, 'sources':sources, 'requirements':requirements,
              'review_issues':sorted(set(issues)), 'request_attachments':refs}
    # Control messages and transcript growth must not invalidate acceptance.
    result['revision'] = digest({k:result[k] for k in ('sources','requirements','review_issues')})
    from project_requirement_lifecycle import apply
    result = apply(workspace, result, value['requests'])
    if persist and result != previous:
        if previous:
            directory = safe_path(workspace, 'requirements-catalog-history'); directory.mkdir(exist_ok=True)
            archived = safe_path(workspace, 'requirements-catalog-history/' + digest(previous) + '.json')
            if not archived.exists(): atomic_json(archived, previous)
        atomic_json(path, result)
    return result
