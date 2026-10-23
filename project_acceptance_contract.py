"""Optional host-reviewed atomic obligations, separate from model declarations.

No fuzzy semantic inference: exact source clauses and exact executed test IDs,
with protected assertion-file hashes. Unconfigured projects keep their workflow.
"""
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract(workspace):
    if not hasattr(workspace,'_acceptance_contract'):
        path=workspace.meta/'acceptance-contract.json'
        value=None
        if path.exists():
            if path.is_symlink() or path.stat().st_size>65536:raise ValueError('Invalid acceptance contract')
            value=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value,dict) or set(value)!={'version','obligations'} or value['version']!=1 or not isinstance(value['obligations'],list) or not 1<=len(value['obligations'])<=128:
                raise ValueError('Invalid acceptance obligation schema')
            for row in value['obligations']:
                if not isinstance(row,dict) or set(row)!={'source','behavior','test','file','sha256'} or any(not isinstance(v,str) or not v for v in row.values()):raise ValueError('Invalid atomic acceptance obligation')
                if not row['test'].startswith(('python:','node:')) or '*' in row['test']:raise ValueError('Exact test ID required')
                if digest(workspace.path(row['file']))!=row['sha256']:raise ValueError('Acceptance assertion source changed')
            value={**value,'path':str(path),'sha256':digest(path)}
        workspace._acceptance_contract=value
    value=workspace._acceptance_contract
    if value and (digest(Path(value['path']))!=value['sha256'] or any(digest(workspace.path(row['file']))!=row['sha256'] for row in value['obligations'])):
        raise ValueError('Pinned acceptance obligations changed during the task')
    return value


def obligations(owner, quote):
    value=contract(owner.workspace)
    clauses={' '.join(part.split()) for part in quote.splitlines()}
    return [row for row in (value or {}).get('obligations',[]) if ' '.join(row['source'].split()) in clauses]


def required(owner, quote):
    rows=obligations(owner,quote)
    return [row['test'] for row in rows] + (['host:full_test_suite_passed'] if rows else [])
