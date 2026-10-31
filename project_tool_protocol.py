"""Durable, build/template-scoped tool protocol selection; no inference probes."""
import functools
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'daily/model-tool-protocols.json'
LOCK = threading.RLock()


def stamp(path):
    path = Path(path)
    stat = path.stat()
    return [str(path.resolve()).casefold(), stat.st_size, stat.st_mtime_ns]


@functools.lru_cache(maxsize=32)
def binary_hash(path, size, modified):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def protocol_identity(policy):
    active = policy.get('active') or {}
    if active.get('engine') == 'tabbyapi':
        from model_tabby import identity
        material = {'version':2, 'model':identity(active), 'loaded_engine':policy.get('engine_identity'),
                    'generation':(policy.get('parameters') or {}).get('values', {}).get('generation', {})}
        return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    model = Path(active['model']).resolve()
    template = ''
    for row in (policy.get('library') or {}).get('models') or []:
        if str(row.get('path', '')).casefold() == str(model).casefold():
            template = (row.get('metadata') or {}).get('tokenizer.chat_template', '')
            break
    engine = []
    for name in ('llama-server.exe', 'llama-server-impl.dll', 'llama-common.dll'):
        path, size, modified = stamp(ROOT / 'runtime' / name)
        engine.append([name, binary_hash(path, size, modified)])
    values = ((policy.get('parameters') or {}).get('values') or {}).get('generation') or {}
    material = {'version': 1, 'model': stamp(model), 'template': template,
                'template_override': active.get('chat_template'), 'engine': engine,
                'thinking': values.get('thinking', 'auto'), 'effort': values.get('reasoning_effort', 'default')}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_profiles():
    if not CACHE.exists():
        return {'version': 1, 'profiles': {}}
    if CACHE.stat().st_size > 8 * 1024**2:
        raise ValueError('Tool protocol cache is too large; preserved without overwriting')
    value = json.loads(CACHE.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('profiles'), dict):
        raise ValueError('Invalid tool protocol cache; preserved without overwriting')
    if any(not isinstance(row, dict) or row.get('mode') not in {'auto', 'text'}
           for row in value['profiles'].values()):
        raise ValueError('Invalid tool protocol profile; preserved without overwriting')
    return value


def remember_protocol(identity, mode, *, reason, rejected_route=None):
    if mode not in {'auto', 'text'}:
        raise ValueError('Unsupported tool protocol mode')
    with LOCK:
        value = read_profiles()
        previous = value['profiles'].get(identity) or {}
        if previous.get('mode') == 'text':
            mode = 'text'  # Never reintroduce a rejected grammar through a downgrade.
        rejected = list(previous.get('rejected_routes') or [])
        if rejected_route and rejected_route not in rejected:
            rejected.append(rejected_route)
        value['profiles'][identity] = {'mode': mode, 'reason': reason, 'rejected_routes': rejected, 'updated': time.time()}
        CACHE.parent.mkdir(exist_ok=True)
        temporary = CACHE.with_name(CACHE.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, CACHE)
        finally:
            temporary.unlink(missing_ok=True)
    return mode


def select_protocol(state, policy):
    """Use known outcomes before the request; retain a legacy task's evidence."""
    identity = protocol_identity(policy)
    old = state.get('tool_compatibility') or {}
    same = old.get('identity') == identity
    legacy = old.get('identity') == [str((policy.get('active') or {}).get('model', '')), policy.get('pid')]
    warnings = []
    profile = {}
    try:
        with LOCK:
            profile = read_profiles()['profiles'].get(identity) or {}
        if profile.get('mode') not in {'auto', 'text', None}:
            raise ValueError('Invalid saved tool protocol mode; no profile applied')
        if legacy and old.get('mode') in {'auto', 'text'}:
            mode = remember_protocol(identity, old['mode'], reason='existing_task_grammar_recovery')
            profile = {'mode': mode}
    except (OSError, ValueError, TypeError) as error:
        warnings.append(str(error))
        profile = {}
    original = old.get('original_protocol', state.get('protocol', 'native')) if same or legacy else state.get('protocol', 'native')
    if old and not (same or legacy):
        name = str((policy.get('active') or {}).get('model', '')).lower()
        original = 'json' if 'gemma-4' in name or 'gemma4' in name else 'native'
    candidates = [profile.get('mode'), old.get('mode') if same or legacy else None]
    mode = 'text' if 'text' in candidates or (policy.get('active') or {}).get('engine') == 'tabbyapi' else 'auto'
    # Once a task rejects native grammar, generic JSON retries must not enable
    # schema grammar again. The JSON protocol is still checked locally.
    if mode == 'auto' and original == 'native' and state.get('protocol') == 'json' and (same or legacy):
        mode = 'text'
    state['protocol'] = 'json' if mode == 'text' else original
    selected = {'identity': identity, 'mode': mode, 'original_protocol': original,
                'selection': 'saved' if profile else 'task' if same or legacy else 'automatic'}
    state['tool_compatibility'] = selected
    return selected, warnings
