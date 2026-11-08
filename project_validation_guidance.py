"""Expose actual automatic test receipts before asking the model to query again."""
import json

def passing_context(workspace, report, catalog, *, character_limit=10000):
    from project_acceptance import host_only_requirements
    if not report.get('success') or report.get('in_progress') or not report.get('source_unchanged_during_validation') or report.get('fingerprint')!=workspace.fingerprint(force=True):
        return ''
    tests=list(dict.fromkeys(test for check in report.get('checks',[])
        if check.get('passed') and check.get('executed',0)>0 and check.get('validation_run_id')
        for test in check.get('successful_tests',[])))
    pending=set(catalog.get('pending_obligation_reviews',[]))
    blocks=[]
    for row in catalog.get('source_requirements',[]):
        if row['id'] not in pending:continue
        text=row['text']
        blocks.append({'requirement_id':row['id'],'text':text[:1600],
            'text_truncated':len(text)>1600,'source_length':len(text),
            'supported_host_evidence':host_only_requirements([row]).get(row['id'],[])})
        if len(blocks)==12:break
    value={'successful_tests':tests[:32],'successful_tests_total':len(tests),
        'catalog_revision':catalog.get('revision'),'pending_source_reviews':len(pending),'source_blocks':blocks,
        'next_action':'Review the next small batch against actual assertions, then save relevant links with acceptance_plan(complete=false), adding merge=true for an existing draft. If source text is truncated, read its full original text before review. Use acceptance_plan to find omitted tests or the next unmapped batch. Revalidate after source or external input changes. Every requirement still needs relevant fresh evidence before delivery.'}
    while len(json.dumps(value, ensure_ascii=False)) > character_limit:
        if len(value['source_blocks']) > 1:
            value['source_blocks'].pop()
        elif value['successful_tests']:
            value['successful_tests'].pop()
        elif value['source_blocks']:
            value['source_blocks'].pop()
        else:
            break
    return '\nCurrent executed evidence and next missing metadata: '+json.dumps(value,ensure_ascii=False)
