"""Optional host-configured performance contracts, pinned for the whole task.

The host supplies the command and reference files. The agent supplies neither
timing thresholds nor pass flags. Projects without a contract keep old behavior.
"""
import hashlib
import json
from pathlib import Path
from project_validation import new_run

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def load_contract(workspace, filename='performance-contract.json'):
    path=workspace.meta/filename
    if not path.exists():return None
    if path.is_symlink() or path.stat().st_size>65536:raise ValueError('Invalid performance contract path or size')
    raw=path.read_bytes();value=json.loads(raw)
    if not isinstance(value,dict) or set(value)!={'version','argv','pinned_files','timeout_seconds'} or value['version']!=1:
        raise ValueError('Invalid performance contract schema')
    argv=value['argv'];pins=value['pinned_files'];timeout=value['timeout_seconds']
    if not isinstance(argv,list) or not 2<=len(argv)<=32 or any(not isinstance(a,str) or not a or len(a)>4096 for a in argv):
        raise ValueError('Performance argv must be a bounded argument list')
    if type(timeout) is not int or not 1<=timeout<=120:raise ValueError('Invalid performance timeout')
    if not isinstance(pins,dict) or not pins or len(pins)>256:raise ValueError('Performance reference files must be pinned')
    for name,sha in pins.items():
        p=Path(name)
        if not p.is_absolute() or p.is_symlink() or p.resolve().is_relative_to(workspace.root) or not isinstance(sha,str) or len(sha)!=64 or digest(p)!=sha:
            raise ValueError('Invalid or modified external performance reference: '+name)
    # Pin the invoked script as well as its reference implementation. No shell
    # fragments or model-written benchmark scripts are accepted as host evidence.
    if argv[1] not in pins:raise ValueError('Performance command script must be pinned outside the project')
    return {**value,'policy_path':str(path),'policy_sha256':hashlib.sha256(raw).hexdigest()}

def contract_current(contract):
    if contract is None:return True
    try:return digest(contract['policy_path'])==contract['policy_sha256'] and all(digest(name)==sha for name,sha in contract['pinned_files'].items())
    except (OSError,KeyError,TypeError):return False

def performance_check(workspace,contract):
    run_id,directory=new_run(workspace)
    check={'name':'performance_contract','passed':False,'validation_run_id':run_id,
           'contract_sha256':contract['policy_sha256'],'evidence':str((directory/'performance.json').relative_to(workspace.root))}
    try:
        if not contract_current(contract):raise ValueError('Performance contract or reference changed during this task')
        before=workspace.fingerprint(force=True)
        argv=[arg.replace('{project}',str(workspace.root)) for arg in contract['argv']]
        result=workspace.command(argv,contract['timeout_seconds'])
        check.update(result)
        if result.get('timed_out') or result.get('exit_code')!=0:raise ValueError('Performance command failed or timed out: '+str(result.get('stderr',''))[-1200:])
        value=json.loads(result.get('stdout',''))
        if isinstance(value,dict) and isinstance(value.get('measurements'),list):
            check['measurements']=value['measurements']
        if not isinstance(value,dict) or value.get('passed') is not True or not isinstance(value.get('measurements'),list) or not value['measurements'] or not all(isinstance(row,dict) and row.get('passed') is True for row in value['measurements']):
            failed=[{key:row[key] for key in ('case','ratio','maximum_ratio','passed') if key in row} for row in (value.get('measurements') or []) if isinstance(row,dict) and row.get('passed') is not True] if isinstance(value,dict) else []
            raise ValueError('Performance regression or invalid measurements: '+json.dumps(failed or value,ensure_ascii=False)[:2000]+'. Reuse the existing optimized helper or provide an implementation that passes the unchanged contract.')
        if before!=workspace.fingerprint(force=True) or not contract_current(contract):raise ValueError('Source, contract or reference changed during performance measurement')
        check.update(passed=True,measurements=value['measurements'])
    except (OSError,ValueError,TypeError,KeyError) as error:
        check['error']=str(error)
    (directory/'performance.json').write_text(json.dumps(check,ensure_ascii=False,indent=2),encoding='utf-8')
    return check
