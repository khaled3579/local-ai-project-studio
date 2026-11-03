"""Narrow final-response routing; every existing delivery check still runs."""
from project_acceptance import Acceptance,host_evidence

def ready(studio,workspace,job,prompt,state):
    if state.get('finalization_repair_required'):return None
    report=workspace.last_validation()
    catalog=studio.requirements().catalog(studio.sessions)
    proof=host_evidence(workspace,state.get('acceptance_initial_tests',{}),job,browser=state.get('acceptance_browser'))
    acceptance=Acceptance(workspace,job.get('original_prompt',prompt),catalog=catalog,evidence=proof)
    if not acceptance.fresh(report) or not acceptance.mapping_status(report).get('ready_for_final_response'):return None
    if job.get('run_entry') and (not workspace.running or workspace.running.poll() is not None):return None
    if job.get('export') and job['export'].get('source_fingerprint')!=report['fingerprint']:return None
    return {'fingerprint':report['fingerprint'],'catalog_revision':catalog['revision']}

def restrict(body,protocol,checkpoint):
    body['messages']=[*body['messages'],{'role':'user','content':
        'FINAL RESPONSE CHECKPOINT: All mandatory named tests and acceptance links are current. '
        'Return the concise Arabic final report of saved changes, actual test results and limitations now. '
        'Do not run another command, inspect files again, or repeat passing checks. '
        'The host will independently validate this final response and every delivery gate. '
        + ('Use exactly {"tool":"final","message":"Arabic report"}.' if protocol=='json' else
           'Use a normal final assistant response without any function or tool call.')}]
    if protocol!='json':
        body.pop('tools',None);body.pop('parallel_tool_calls',None);body['tool_choice']='none'
    return body
