"""Durable progress accounting and bounded continuation hints; never acceptance."""
import hashlib
import json
import os
from pathlib import Path
import time

READS={'read_file','list_files','search_project','lookup_docs','read_project_requirements','read_continuation','read_attachment'}
VALIDATION={'validate_project','diagnose_project','browser_project'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def tracker(job):
    state=job.setdefault('efficiency',{'version':1,'started':job.get('started',time.time()),
        'actions':0,'progress_events':0,'categories':{},'repeat_candidates':0,
        'reused_reads':0,'reused_execution':0,'context_transitions':[],
        'completed_requirement_ids':[],'remaining_requirement_ids':[],
        'seen_actions':[],'evidence_keys':[],'recovery_started':{},'recovery_resolved_after_progress':{},
        'recovery_repeated_failures':{},'pending_recovery':None,'history':[]})
    state.setdefault('pending_recoveries',[state['pending_recovery']] if state.get('pending_recovery') else [])
    state.setdefault('recovery_failure_at',{})
    state.setdefault('repeat_without_progress_candidates',0)
    return state


def failure(state,recovery):
    pending=state['pending_recoveries']
    key='recovery_repeated_failures' if recovery in pending else 'recovery_started'
    state[key][recovery]=state[key].get(recovery,0)+1
    if recovery not in pending:pending.append(recovery)
    state['recovery_failure_at'][recovery]=time.time()
    state['pending_recovery']=pending[-1]


def resolved(state,recovery,counter):
    if recovery not in state['pending_recoveries']:return
    state['pending_recoveries'].remove(recovery)
    counts=state.setdefault(counter,{})
    counts[recovery]=counts.get(recovery,0)+1
    state['pending_recovery']=state['pending_recoveries'][-1] if state['pending_recoveries'] else None


def lane(name,result):
    if name=='transport_error':return 'transport'
    if name in {'generation_loop','generation_limit','protocol_error'}:return 'protocol'
    if name in {'acceptance_plan','review_project_requirements'}:return 'acceptance'
    return 'project'


def next_step(state):
    recovery=state.get('pending_recovery')
    if recovery:
        return {'action':{'transport':'restore connection and resume the saved cursor',
            'protocol':'correct only the rejected tool envelope; no source repair',
            'acceptance':'repair only missing requirement review or evidence links',
            'project':'inspect the first actual failing diagnostic and patch its cause'}[recovery],
            'reason':'Unresolved '+recovery+' failure; preserve completed actions and recorded failures.'}
    if state.get('remaining_requirement_ids'):
        return {'action':'continue the next unmet requirement or obtain its missing evidence',
                'requirement_id':state['remaining_requirement_ids'][0],
                'reason':'Reads alone do not complete requirements; reuse current valid evidence.'}
    return {'action':'check final acceptance and report actual results',
            'reason':'Only the existing fresh validation and acceptance gates may authorize delivery.'}


def update_requirements(job,requirements,coverage=None,fingerprint=None):
    state=tracker(job);all_ids=[r['id'] for r in requirements]
    complete={key for row in (coverage or {}).get('items',[]) if row.get('passed')
              for key in row.get('requirement_ids',[])} if (coverage or {}).get('validation_current') else set()
    # Evidence changes invalidate completion; a remembered green row is not proof.
    state['completed_requirement_ids']=[key for key in all_ids if key in complete]
    state['remaining_requirement_ids']=[key for key in all_ids if key not in complete]
    state['requirements_fingerprint']=fingerprint
    change=state.get('last_source_change')
    if complete and change and change['fingerprint']==fingerprint:
        state.setdefault('first_useful_edit_at',change['time'])
    state['next_step']=next_step(state)


def observe(job,name,arguments,result,before,after,*,epoch=0,browser_fingerprint=None):
    state=tracker(job);now=time.time()
    category='reading' if name in READS else 'verification' if name in VALIDATION else 'acceptance' if name in {'acceptance_plan','review_project_requirements'} else 'protocol' if name.startswith('generation_') else 'execution'
    key=digest([name,arguments,after,epoch if name in READS else None])
    repeated=key in state['seen_actions']
    state['seen_actions']=(state['seen_actions']+[key])[-256:]
    state['actions']+=1;state['categories'][category]=state['categories'].get(category,0)+1
    if repeated:state['repeat_candidates']+=1
    if result.get('reused_read'):state['reused_reads']+=1
    if result.get('reused_execution'):state['reused_execution']+=1
    detail=result.get('result',result)
    failed=bool(result.get('error') or result.get('timed_out') or result.get('exit_code',0) not in (None,0)
                or result.get('success') is False or result.get('missing_evidence')
                or isinstance(detail,dict) and detail.get('assertion',{}).get('passed') is False)
    progress=[];evidence=[]
    if before!=after:
        progress.append('source_changed')
        state.setdefault('first_source_change_at',now)
        state['last_source_change']={'time':now,'fingerprint':after}
        # All old requirement completion needs re-evaluation on new source.
        if state.get('requirements_fingerprint')!=after:
            state['remaining_requirement_ids']=list(dict.fromkeys(state['remaining_requirement_ids']+state['completed_requirement_ids']))
            state['completed_requirement_ids']=[]
    if name=='validate_project' and result.get('success') is True and result.get('fingerprint')==after and result.get('source_unchanged_during_validation') is True:
        for check in result.get('checks',[]):
            if check.get('passed') and check.get('executed',0)>0 and check.get('validation_run_id'):
                for identity in check.get('successful_tests',[]):
                    evidence.append(digest(['test',identity,after,result.get('validator_version'),result.get('quality_contract_sha256'),result.get('performance_contract_sha256')]))
    if name=='browser_project' and isinstance(detail,dict) and detail.get('assertion',{}).get('passed') is True and browser_fingerprint==after:
        evidence.append(digest(['browser',arguments,after]))
    new=[key for key in evidence if key not in state['evidence_keys']]
    if new:progress.append('new_valid_evidence')
    state['evidence_keys']=(state['evidence_keys']+new)[-2048:]
    if name=='acceptance_plan' and result.get('validation_current') and result.get('saved') and result.get('mapping_status',{}).get('ready_for_final_response'):
        key=digest(['acceptance',result.get('request_key'),after])
        if key not in state['evidence_keys']:
            progress.append('accepted_mapping');state['evidence_keys'].append(key)
    if progress:state['progress_events']+=1
    reused=bool(result.get('reused_execution') or result.get('reused_read'))
    if repeated and not progress and not reused:state['repeat_without_progress_candidates']+=1
    if failed:
        failure(state,lane(name,result))
    elif name=='validate_project' and result.get('success') is True and result.get('fingerprint')==after and result.get('source_unchanged_during_validation') is True and evidence:
        resolved(state,'project','recovery_resolved_after_progress')
    elif name=='acceptance_plan' and result.get('validation_current') and result.get('mapping_status',{}).get('ready_for_final_response'):
        resolved(state,'acceptance','recovery_resolved_after_progress')
    row={'number':state['actions'],'time':now,'tool':name,'category':category,
         'repeated_on_same_source':repeated,'progress':progress,
         'outcome':'failed' if failed else 'not_executed' if result.get('not_executed') else 'waiting_for_user' if result.get('waiting_for_user') else 'completed',
         'before_fingerprint':before,'after_fingerprint':after,
         'action_sha256':key,'reused':reused,
         'repeat_without_progress_candidate':repeated and not progress and not reused}
    state['last_action']=row;state['history']=(state['history']+[row])[-128:]
    state['next_step']=next_step(state)
    return row


def generation_outcome(job,measurement):
    state=tracker(job);outcome=measurement['outcome']
    if outcome in {'connection_error','http_error','incomplete','length'}:
        recovery='transport' if outcome=='connection_error' else 'protocol' if outcome=='length' or measurement.get('http_status') in {400,422} else 'transport'
        failure(state,recovery)
    elif outcome=='complete':
        # This is only transport completion; source progress and validated tool
        # syntax are recorded separately, so a stream alone grants no success.
        state['last_completed_stream_at']=time.time()
    state['next_step']=next_step(state)


def valid_action(job):
    state=tracker(job)
    for recovery in ('transport','protocol'):
        if state.get('last_completed_stream_at',0)>=state['recovery_failure_at'].get(recovery,float('inf')):
            resolved(state,recovery,'recovery_valid_action_after_failure')
    state['next_step']=next_step(state)


def continuation(job):
    state=tracker(job)
    return {'completed':state['completed_requirement_ids'],'remaining':state['remaining_requirement_ids'],
            'last_action':state.get('last_action'),'next_step':state.get('next_step',next_step(state)),
            'pending_recovery':state['pending_recovery'],
            'pending_recoveries':list(state['pending_recoveries']),
            'evidence_fingerprint':state.get('requirements_fingerprint'),
            'note':'Progress summary only; re-evaluate evidence freshness and mandatory delivery gates.'}


def metrics(job):
    state=tracker(job);totals=job.get('token_usage',{});completed=len(state['completed_requirement_ids'])
    all_measured=bool(totals.get('requests')) and totals.get('requests')==totals.get('measured_requests')
    request_count=totals.get('requests')
    return {'completed_requirements':completed,'repeat_candidates':state['repeat_candidates'],
        'repeat_without_progress_candidates':state['repeat_without_progress_candidates'],
        'requests_per_completed_requirement':request_count/completed if completed and type(request_count) is int else None,
        'tokens_per_completed_requirement':(totals.get('prompt_tokens',0)+totals.get('completion_tokens',0))/completed if completed and all_measured else None,
        'first_useful_edit_seconds':state['first_useful_edit_at']-state['started'] if 'first_useful_edit_at' in state else None,
        'first_source_change_seconds':state['first_source_change_at']-state['started'] if 'first_source_change_at' in state else None,
        'delivery_seconds':state['delivered_at']-state['started'] if 'delivered_at' in state else None,
        'usage_complete':all_measured,'measured_savings':None}


def cached_read(state,workspace,name,arguments,invoke,epoch):
    """Bounded saved views, verified against current source bytes and read epoch."""
    if name!='read_file':return invoke()
    # Hash the requested file, not the whole project. Source views depend on
    # these exact bytes. Epoch distinguishes a context recovery reread.
    path=workspace.path(arguments.get('path',''))
    if not path.is_file() or path.stat().st_size>2*1024*1024:return invoke()
    with path.open('rb') as stream:
        fingerprint=hashlib.file_digest(stream,'sha256').hexdigest()
    key=digest([str(workspace.root),arguments,fingerprint,epoch]);cache=state.setdefault('read_cache',{})
    if key in cache:return {**cache[key],'reused_read':True}
    result=invoke()
    if not result.get('error') and result.get('sha256')==fingerprint and len(json.dumps(result,ensure_ascii=False))<=12000:
        if len(cache)>=4:cache.pop(next(iter(cache)))
        cache[key]=result
    return result


def execution_context(workspace):
    """Do not store environment values/secrets in a continuation."""
    from project_validation import COMMAND_ENV
    environment={**os.environ,**COMMAND_ENV.get()}
    pins=[]
    for relative in ('.venv/pyvenv.cfg','.venv/Scripts/python.exe','.venv/bin/python'):
        path=workspace.root/relative
        if path.is_file():
            stat=path.stat();pins.append([relative,stat.st_size,stat.st_mtime_ns])
    for path in sorted((workspace.root/'.venv/Lib/site-packages').glob('*.dist-info/RECORD')):
        stat=path.stat();pins.append([path.name,str(path.parent.name),stat.st_size,stat.st_mtime_ns])
    return digest([environment,pins])
