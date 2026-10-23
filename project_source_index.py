"""Bounded, content-addressed JS/TS syntax index using the bundled parser."""
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import threading

ROOT = Path(__file__).resolve().parent
_lock = threading.Lock()


@lru_cache(maxsize=8)
def _javascript_index(text, suffix):
    result = subprocess.run([str(ROOT/'runtime/node/node.exe'), str(ROOT/'project_source_index.js')],
                            input=json.dumps({'content': text, 'suffix': suffix}, ensure_ascii=False),
                            capture_output=True, text=True, encoding='utf-8', errors='strict', timeout=12,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise ValueError('Source parser unavailable: ' + result.stderr[:300])
    value = json.loads(result.stdout)
    if value.get('error'):
        raise ValueError('Cannot index invalid JavaScript/TypeScript; use line ranges: ' + json.dumps(value.get('diagnostics', [])))
    return tuple(tuple(sorted(row.items())) for row in value['definitions']), value['accuracy']


def javascript_index(text, suffix):
    # The small cache bounds retained source to <=16 MiB (8 x the read limit).
    # Serialize cache fills so concurrent UI/agent reads don't spawn duplicate parsers.
    with _lock:
        rows, accuracy = _javascript_index(text, suffix.lower())
    return [dict(row) for row in rows], accuracy
