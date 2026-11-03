"""Reuse current real validation receipts; never synthesize test execution."""
from project_progress import digest,execution_context
from project_validation import current_passing_validation


def identity(report):
    return digest({k:v for k,v in report.items() if k not in {'current','validation_outdated','reused_validation'}})


def remember(state,workspace,result,environment):
    report=workspace.last_validation()
    if result.get('success') and report and report.get('success') and result.get('fingerprint')==report.get('fingerprint') and environment==execution_context(workspace):
        state['validation_reuse_receipt']={'environment':environment,'report':identity(report)}
    else:state.pop('validation_reuse_receipt',None)


def validate(state,workspace,arguments,invoke,not_before=None):
    if set(arguments)-{'force','reason'}:raise ValueError('Validation accepts force and reason only')
    force=arguments.get('force',False)
    if type(force) is not bool:raise ValueError('force must be a boolean')
    if force and (not isinstance(arguments.get('reason'),str) or len(arguments['reason'].strip())<10):
        raise ValueError('Forced repeated validation needs a concrete reason of at least 10 characters')
    environment=execution_context(workspace);saved=state.get('validation_reuse_receipt')
    if not force and saved and saved.get('environment')==environment:
        current=current_passing_validation(workspace,not_before)
        if current is not None and saved.get('report')==identity(current):
            return {**current,'reused_validation':True,
                'next_action':'Reuse these exact executed test IDs. Repair only missing source reviews or acceptance links; unchanged passing tests need no rerun. For changed external test inputs request force=true with a concrete reason.'}
    result=invoke();remember(state,workspace,result,environment)
    return result
