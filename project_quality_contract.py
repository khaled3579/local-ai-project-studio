"""Host-pinned executable quality obligations; model pass declarations do not count."""
import json
from project_performance import load_contract as load_pinned_contract,contract_current
from project_validation import new_run


def load_contract(workspace):
    return load_pinned_contract(workspace,'quality-contract.json')


def quality_check(workspace,contract):
    run_id,directory=new_run(workspace)
    check={'name':'quality_contract','passed':False,'validation_run_id':run_id,
           'contract_sha256':contract['policy_sha256'],
           'evidence':str((directory/'quality.json').relative_to(workspace.root))}
    try:
        if not contract_current(contract):raise ValueError('Quality contract or reference changed')
        before=workspace.fingerprint(force=True)
        result=workspace.command([arg.replace('{project}',str(workspace.root)) for arg in contract['argv']],contract['timeout_seconds'])
        check.update(result)
        if result.get('timed_out'):raise ValueError('Quality contract timed out; no passing receipt')
        value=json.loads(result.get('stdout',''))
        rows=value.get('checks') if isinstance(value,dict) else None
        if not isinstance(rows,list) or not rows or any(not isinstance(row,dict) or type(row.get('passed')) is not bool or not isinstance(row.get('name'),str) for row in rows):
            raise ValueError('Invalid executable quality receipt')
        check['checks']=rows
        if result.get('exit_code')!=0 or value.get('passed') is not True or not all(row['passed'] for row in rows):
            failed=[row for row in rows if not row['passed']]
            raise ValueError('Generated-test quality blocked: '+json.dumps(failed,ensure_ascii=False)[:5000]+'. Correct test oracles/harnesses; do not rewrite passing implementation to satisfy a defective expected value.')
        if before!=workspace.fingerprint(force=True) or not contract_current(contract):raise ValueError('Source or quality policy changed during audit')
        check['passed']=True
    except (OSError,ValueError,TypeError,KeyError) as error:
        check['error']=str(error)
    (directory/'quality.json').write_text(json.dumps(check,ensure_ascii=False,indent=2),encoding='utf-8')
    return check
