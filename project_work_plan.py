"""Host-maintained execution stages and a regenerated Arabic Markdown view."""
import hashlib
import json
from pathlib import Path
import re
import time

from app_maintenance import atomic_json
from project_core import tool

EVIDENCE_KINDS = ('source_review', 'artifact_review', 'acceptance', 'validation')


def observe_plan_validation(state, catalog_revision, plan_revision, rejected=False):
    """Only count rejected plans against the same current source/plan state."""
    scope = [catalog_revision, plan_revision]
    if state.get('plan_validation_scope') != scope:
        state['plan_validation_scope'] = scope
        state['plan_validation_failures'] = 0
    if rejected:
        state['plan_validation_failures'] += 1
    return rejected and state['plan_validation_failures'] >= 4


class PlanValidationError(ValueError):
    def __init__(self, invalid, current_ids):
        super().__init__('Use current requirement IDs for every stage; replace the invalid references shown in plan_repair.')
        ids = sorted({key for values in invalid.values() for key in values})
        self.plan_repair = {
            'invalid_requirement_ids': [key[:96] for key in ids[:64]],
            'invalid_requirement_count': len(ids),
            'invalid_requirement_ids_truncated': len(ids)>64 or any(len(key)>96 for key in ids),
            'invalid_stage_ids': list(invalid)[:16],
            'invalid_stage_count': len(invalid),
            'current_requirement_count': len(current_ids),
            'batch_saved': False,
            'next_action': 'Use current_requirement_ids from the current preparation gate, or read '
                'read_project_requirements pages. Replace retired/context/unknown IDs in every listed stage. '
                'Preserve the actual requirements and existing stage criteria; no invalid plan was saved.'}
PREPARATION_TOOLS = frozenset({
    'project_plan', 'list_files', 'read_file', 'search_project', 'read_attachment',
    'read_project_requirements', 'review_project_requirements', 'review_atomic_requirement', 'read_continuation',
    'ask_user', 'lookup_docs', 'git_inspect', 'database_read', 'diagnose_project',
})


def preparation_tools(tools, gate):
    """Do not offer implementation tools while their host gate is closed."""
    if not gate:
        return tools
    import copy
    result = copy.deepcopy([entry for entry in tools if entry['function']['name'] in PREPARATION_TOOLS])
    if gate.get('next_source_batch', {}).get('pending_source_reviews', 0):
        # Planning is not executable until semantic source review is complete.
        # Offer source readers/reviewers, never an attractive premature save.
        from project_requirement_review_tool import scoped_review_tools
        # Reading and classifying existing sources does not require permission
        # to continue the already authorized task. Essential implementation
        # questions remain available after this review gate opens.
        source_tools = {'read_project_requirements', 'read_attachment',
                        'review_project_requirements', 'review_atomic_requirement'}
        blocks = gate['next_source_batch'].get('source_blocks', [])
        if blocks and all(not row.get('text_truncated') for row in blocks):
            # Exact source text is already in this request. Readers return when
            # a page is truncated, rather than rereading offset zero forever.
            source_tools -= {'read_project_requirements', 'read_attachment'}
        return scoped_review_tools([entry for entry in result if entry['function']['name'] in source_tools],
                                   gate['next_source_batch'])
    draft = gate.get('draft')
    if draft and not draft.get('stale') and not gate.get('open_question_count'):
        action = 'append_stages' if draft['missing_count'] else 'finalize_draft'
        for entry in result:
            if entry['function']['name'] == 'project_plan':
                # The gate already carries the exact current draft, saved IDs
                # and next source requirements. Another read cannot advance it.
                entry['function']['parameters']['properties']['action']['enum'] = [action]
                if action == 'append_stages':
                    entry['function']['parameters']['properties']['stages'].update(minItems=1, maxItems=1)
    return result


STAGE = {'type': 'object', 'properties': {
    'id': {'type': 'string', 'description': 'A unique short stable ID such as S1.'}, 'title': {'type': 'string'},
    'depends_on': {'type': 'array', 'maxItems': 40, 'items': {'type': 'string'}},
    'requirement_ids': {'type': 'array', 'minItems': 1, 'maxItems': 256, 'items': {'type': 'string'}},
    'deliverables': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string'},
                     'description': 'Exact relative FILE paths only, such as app.py or design.md. Never descriptions, commands, inspection results or test receipts. Use [] for source-review stages.'},
    'criteria': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': {'type': 'string'}},
    'evidence_kind': {'type': 'string', 'enum': list(EVIDENCE_KINDS)}},
    'required': ['id', 'title', 'depends_on', 'requirement_ids', 'deliverables', 'criteria', 'evidence_kind'],
    'additionalProperties': False}
WORK_PLAN_TOOL = tool('project_plan',
    'For an existing plan use update_stage with ONE complete current stage and a concrete reason. The host merges it into the full graph and preserves criteria. save replaces the ENTIRE graph; never send one existing stage to save. '
    'Build the initial plan in small batches: append_stages saves ONE stage covering at most 16 IDs from draft.next_requirements. '
    'Do not regenerate prior stages. Reference saved stage IDs for dependencies. finalize_draft publishes only after ALL IDs are covered. '
    'Drafts do not permit implementation. read returns the saved draft. '
    'Read or save the project execution plan before implementation. The host generates .local-ai/PROJECT_PLAN.md. '
    'Read original requirements and review sources first. Save a dependency graph covering ALL current IDs, '
    'with concrete deliverables and completion criteria. Use source_review for understanding, artifact_review for '
    'design documents (never executable code), acceptance for implemented behavior, and validation for the final all-requirements check. Only acceptance-stage criteria must match the actual '
    'acceptance_plan criterion texts. Save a valid plan before writing or executing project commands. '
    'review_artifacts records YOUR review of documents and their current hashes, never human approval. '
    'The host activates the next ready stage automatically after saving and after valid completion evidence. '
    'Keep small projects to a few useful stages; final validation is a delivery gate, not a separate artificial criterion. '
    'Stage completion is computed from evidence. Deliverables are exact FILE paths, never descriptions or command results. '
    'block records an obstacle and next action. '
    'Changed scope needs a revised plan and reason. reopen requires a concrete reason. '
    'Use update_context to document resolved decisions and the remaining unresolved questions without resending stages. '
    'It appends assumptions and preserves every stage and criterion. Never send stage_id to update_context; include assumptions or open_questions. For stage blockers use block with stage_id, reason and next_action. Answered questions must not remain open. '
    'Reuse passing evidence. A blocked retry limit requires an explicit revised approach.',
    {'action': {'type': 'string', 'enum': ['read', 'append_stages', 'finalize_draft', 'update_stage', 'save', 'start', 'block', 'reopen', 'review_artifacts', 'update_context']},
     'expected_draft_revision': {'type': 'integer', 'minimum': 0},
     'expected_revision': {'type': 'integer', 'minimum': 0,
                           'description': 'The agent host fills the current revision when omitted. An explicitly stale revision is still rejected.'},
     'stages': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': STAGE},
     'assumptions': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'},
                     'description': 'Document implementation assumptions and already resolved decisions. update_context appends these to existing assumptions.'},
     'open_questions': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string'},
                        'description': 'Only UNANSWERED essential questions. Any entry blocks execution. In update_context provide only questions that remain unresolved; [] means all were resolved and documented.'},
     'stage_id': {'type': 'string'},
     'reason': {'type': 'string', 'description': 'Required concrete reason for updating an existing plan, update_context, block, reopen, or review_artifacts.'},
     'next_action': {'type': 'string'}}, [])


def action_scoped_plan_tools(tools):
    """Expose only executable field combinations after other plan scope filters."""
    import copy
    result = copy.deepcopy(tools)
    contracts = {
        'read': (['stage_id'], []),
        'append_stages': (['stages','assumptions','open_questions','reason','expected_draft_revision'], ['stages']),
        'finalize_draft': (['expected_draft_revision','reason'], []),
        'update_stage': (['stages','reason','expected_revision'], ['stages','reason']),
        'save': (['stages','assumptions','open_questions','reason','expected_revision'], ['stages']),
        'start': (['stage_id','reason','next_action','expected_revision'], ['stage_id']),
        'block': (['stage_id','reason','next_action','expected_revision'], ['stage_id','reason','next_action']),
        'reopen': (['stage_id','reason','next_action','expected_revision'], ['stage_id','reason']),
        'review_artifacts': (['stage_id','reason','expected_revision'], ['stage_id','reason']),
        'update_context': (['assumptions','open_questions','reason','expected_revision'], ['reason']),
    }
    for entry in result:
        if entry['function']['name'] != 'project_plan':
            continue
        schema = entry['function']['parameters']; fields = schema['properties']; options = []
        for action in fields['action']['enum']:
            allowed, required = contracts[action]
            for extra in (['assumptions','open_questions'] if action == 'update_context' else [None]):
                properties = {'action': {'type':'string','const':action}}
                properties.update({key:copy.deepcopy(fields[key]) for key in allowed if key in fields})
                if not set(required) <= set(properties) or extra and extra not in properties:
                    continue
                if action in {'append_stages','update_stage'}:
                    properties['stages'].update(minItems=1,maxItems=1)
                options.append({'type':'object','properties':properties,
                    'required':['action',*required,*([extra] if extra else [])], 'additionalProperties':False})
        schema['anyOf'] = options
        entry['function']['description'] += ' Each action has separate allowed fields. update_context is global decisions/questions only: reason plus assumptions or open_questions, never stage_id. To record a stage obstacle use block(stage_id, reason, next_action); to fix implementation use read_file then patch_file, not update_context.'
    return result


def enforce_plan_action_call(choice, tools):
    from project_generation import validate_action_value, ActionFormatError
    for call in (choice.get('message') or {}).get('tool_calls') or []:
        function = call.get('function') or {}
        if function.get('name') != 'project_plan':
            continue
        schema = next((entry['function']['parameters'] for entry in tools
                       if entry['function']['name'] == 'project_plan'), None)
        if schema is None:
            raise ActionFormatError('project_plan is unavailable in this request')
        try:
            arguments = json.loads(function.get('arguments') or '{}')
        except (ValueError,TypeError):
            raise ActionFormatError('project_plan arguments must be complete JSON') from None
        try:
            validate_action_value(arguments, schema)
        except ActionFormatError:
            if isinstance(arguments,dict) and arguments.get('action') == 'update_context':
                raise ActionFormatError('update_context accepts global decisions/questions only: reason plus assumptions or open_questions, no stage_id/stages/next_action. For a stage obstacle use block with stage_id, reason and next_action. Repair source behavior with read_file then patch_file; do not repeat this context call.') from None
            raise


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def text(value):
    return ' '.join(str(value).split())


def read_document(workspace, arguments):
    """Expose only the generated plan, without making runtime metadata writable."""
    path = workspace.meta/'PROJECT_PLAN.md'
    if path.is_symlink() or not path.resolve().is_relative_to(workspace.meta.resolve()):
        raise ValueError('Invalid work plan document path')
    offset, limit = arguments.get('offset', 0), arguments.get('max_chars', 12000)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 32000:
        raise ValueError('Use a nonnegative offset and max_chars between 1 and 32000')
    if path.is_file() and path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Work plan document exceeds its supported size')
    content = path.read_text(encoding='utf8') if path.exists() else 'Save project_plan first.'
    if arguments.get('symbol') is not None or arguments.get('outline', False) is not False:
        raise ValueError('Generated Markdown supports line ranges and offset pagination; use project_plan for structured stages, not symbol or outline selectors.')
    start, stop = arguments.get('start_line'), arguments.get('end_line')
    for key, value in (('start_line', start), ('end_line', stop)):
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(key + ' must be a positive integer')
    lines = content.splitlines(keepends=True)
    total = len(lines)
    ranged = start is not None or stop is not None
    start = 1 if start is None else start
    stop = total if stop is None else stop
    if start > max(1, total) or (stop < start and total):
        raise ValueError('Line range outside work plan document')
    stop = min(stop, total)
    selected = ''.join(lines[start - 1:stop]) if ranged else content
    if offset > len(selected):
        raise ValueError('Offset outside selected work plan view')
    end = min(len(selected), offset + limit)
    more = end < len(selected)
    selector = {'start_line': start, 'end_line': stop} if ranged else {}
    next_read = {'path': '.local-ai/PROJECT_PLAN.md', **selector,
                 'offset': end, 'max_chars': limit} if more else None
    return {'path': '.local-ai/PROJECT_PLAN.md', 'content': selected[offset:end],
            **selector, 'total_lines': total, 'selected_chars': len(selected),
            'offset': offset, 'next_offset': end if more else None,
            'more': more, 'next_read': next_read,
            'total_chars': len(content), 'metadata_only': True, 'next_tool': 'project_plan',
            'note': 'Offset is relative to this selected view; follow next_read to preserve line bounds. Use project_plan to update stages. Do not patch the generated Markdown.'}



def stage_route(hint, gate=None):
    """Propose, never execute, the next operation for the actual stage state."""
    stage = hint.get('stage') or {}; evidence = stage.get('evidence') or {}
    missing = evidence.get('missing_deliverables') or []
    gaps = evidence.get('observable_gaps') or []
    full = {'tool':'project_plan','arguments':{'action':'read'}}
    route = {'next_call': hint.get('next_call') or full}
    if hint.get('validation_failure'):
        route['validation_failure'] = hint['validation_failure']
    clear = not (hint.get('needs_review') or hint.get('plan_required') or hint.get('open_question_count'))
    if clear and stage.get('status') == 'blocked':
        route['next_call'] = full
        route['after_reopen_next_call'] = {'tool':'project_plan','arguments':{'action':'start','stage_id':stage.get('id')}}
        if stage.get('blocked') == 'Repeated operation or rejected attempts on unchanged stage evidence.':
            approach = ('Implement the confirmed missing deliverables rather than repeat file inventory; preserve existing code and criteria, then validate the new behavior.' if missing else
                        'Implement the missing asserted outcomes and relevant tests instead of querying or relinking unrelated backend receipts; preserve all criteria and existing tests.' if gaps else
                        'Review actual fresh assertions and map exact pending acceptance criteria rather than repeat inventory; preserve source and tests.' if evidence.get('validation_current') is True else
                        'Diagnose the first current validation failure with a focused reproduction rather than repeat inventory, preserving meaningful tests.')
            if hint.get('fresh_proof_candidates_available') and evidence.get('validation_current') is True and not missing:
                approach = 'Review the current fresh candidate assertions after reopening and explicitly starting this stage, then use link_acceptance_evidence to repair exact pending links if relevant. Do not reimplement an outcome merely because its stored link is stale. Candidate availability is not acceptance; preserve criteria and valid links.'
            route['next_call'] = {'tool':'project_plan','arguments':{'action':'reopen','stage_id':stage.get('id'),'reason':approach}}
    elif clear and stage.get('status') == 'active' and missing and not gate:
        route.update(next_call=None,next_tool='write_file',
            next_action='The listed deliverables are confirmed absent. Inspect only related existing dependencies not already read, then generate a complete missing file with write_file. Preserve existing code and every criterion. Repeating unchanged inventory cannot create these deliverables.')
    elif clear and stage.get('status') == 'active' and not missing and evidence.get('validation_current') is False and not gate:
        if evidence.get('validation_attempt_current'):
            route.update(next_call=None, next_tool=None,
                         next_action='A current executed validation failed. Read the actual validation_failure diagnostic and the named implementation/test file, then repair the first failing assertion, import or test setup using a precise patch. Static diagnose_project is NOT the next step merely because tests failed; do not repeat unchanged static reports. Use scoped static diagnostics only for a specific unresolved syntax/type issue. Preserve meaningful assertions and criteria; validate after a real source correction, never mark a static report as passing runtime tests.')
        else:
            route.update(next_call={'tool':'validate_project','arguments':{}}, next_tool='validate_project',
                         next_action='Declared deliverables exist but validation is absent or stale. Run validate_project now, then follow its actual failures or link exact passing evidence. Do not reread or edit the plan to manufacture evidence.')
    elif clear and stage.get('status') == 'active' and gaps and evidence.get('validation_current') is True and hint.get('fresh_proof_candidates_available') and not gate:
        route.update(next_call={'tool':'link_acceptance_evidence','arguments':{}}, next_tool='link_acceptance_evidence',
                     next_action='Current host proof candidates exist for the missing observable. Query link_acceptance_evidence NOW to review their actual assertions, exact evidence IDs and full opaque item_id. Link only a relevant proof using an exact existing_links old_test. Do not implement the same outcome again or invent a test ID. Candidate availability does not grant acceptance; the host still checks scope and relevance.')
    elif clear and stage.get('status') == 'active' and gaps and evidence.get('validation_current') is True and not gate:
        route.update(next_call=None, next_tool=None,
            next_action='Existing mappings lack the listed asserted outcomes. Inspect only related source not already reviewed, implement missing behavior and relevant tests with write_file or patch_file, then validate changed source and link the new exact receipts with link_acceptance_evidence. Do not query the unchanged catalog, relink unrelated backend tests, resave criteria, or rerun unchanged validation. Preserve existing criteria, valid links and tests. UI observations require actual DOM state or fresh browser assertions; CSV/WebP require asserted output content or format.')
        if any('ui-interaction' in row.get('missing',[]) for row in gaps):
            route['next_action'] += ' For ui-interaction, perform click/fill/select/press then assert changed text/value/checked/count. Visibility or enabled alone is structure only.'
    elif clear and stage.get('status') == 'active' and evidence.get('missing_requirement_ids') and evidence.get('validation_current') is True and not missing and not gate:
        route.update(next_call={'tool':'acceptance_plan','arguments':{}}, next_tool='acceptance_plan',
            next_action='Stage criteria match, but listed missing_requirement_ids lack coverage by passing matching criteria. Review these original requirements and current mappings; merge only relevant IDs into exact matching criteria with suitable evidence (complete=false, merge=true). Do not force validation or restart the stage. Preserve existing criteria and links; never attach unrelated IDs merely to clear the gate. If no stage criterion covers a listed requirement, use project_plan to add an appropriate criterion while preserving every existing criterion and dependency, then supply its relevant evidence.')
    elif clear and stage.get('status') == 'active' and hint.get('acceptance_templates') and evidence.get('validation_current') is True and not missing and not gate:
        route['next_call'] = {'tool':'acceptance_plan','arguments':{}}
    return route


def generation_hint(hint, max_chars=3000, *, gate=None):
    """Keep exact evidence templates and the next safe action within prompt limits."""
    if type(max_chars) is not int or max_chars < 1400:
        raise ValueError('Execution hint budget must be at least 1400 characters')
    stage = hint.get('stage') or {}
    evidence = stage.get('evidence') or {}
    templates = json.loads(json.dumps(hint.get('acceptance_templates') or []))
    gaps = evidence.get('observable_gaps') or []
    evidence_ready = bool(templates and evidence.get('validation_current') is True
                          and not evidence.get('missing_deliverables') and not gaps)
    can_map = evidence_ready and stage.get('status') == 'active'
    start_call = {'tool':'project_plan','arguments':{'action':'start','stage_id':stage.get('id')}}
    evidence_call = {'tool':'acceptance_plan','arguments':{}}
    full_plan_call = {'tool':'project_plan','arguments':{'action':'read', **({'stage_id':stage['id']} if stage.get('id') else {})}}
    blocked = stage.get('status') == 'blocked'
    route = stage_route(hint, gate)
    missing = list(evidence.get('missing_deliverables') or [])
    original = json.loads(json.dumps(hint))
    original['full_plan_call'] = full_plan_call
    original['next_call'] = original.get('next_call') or full_plan_call
    if blocked:
        original['next_call'] = full_plan_call
        if evidence_ready:
            original['after_reopen_next_call'] = start_call
    elif can_map:
        original['next_call'] = evidence_call
    original.update(route)
    if route.get('next_action'):
        original['instruction'] = route['next_action']
        if hint.get('validation_failure'):
            original.pop('next_action',None)
    original['missing_deliverables'] = missing
    original['missing_deliverable_count'] = len(missing)
    if gaps:
        original['observable_gaps'] = [{'plan_item_label':r.get('item_id'),'missing':r.get('missing')} for r in gaps[:3]]
        original['observable_gap_count'] = len(gaps)
        original['acceptance_templates'] = []
        original['templates_deferred_until_observables_obtained'] = True
    if len(json.dumps(original,ensure_ascii=False)) <= max_chars:
        return focus_evidence_hint(original, hint, max_chars, gate)
    next_call = full_plan_call if blocked else evidence_call if can_map else (hint.get('next_call') or full_plan_call)
    value = {key:hint.get(key) for key in ('revision','plan_required','needs_review','ready_for_delivery','open_question_count')}
    value.update(next_call=next_call, full_plan_call=full_plan_call,
        path='.local-ai/PROJECT_PLAN.md',
        acceptance_write_options=hint.get('acceptance_write_options', {}),
        acceptance_templates=templates, template_count=len(templates),
        stage={'id':stage.get('id'),'status':stage.get('status'),
               'requirement_count':len(stage.get('requirement_ids', [])),
               'criterion_count':len(stage.get('criteria', [])),
               'deliverable_count':len(stage.get('deliverables', [])),
               'validation_current':evidence.get('validation_current'),
               'missing_deliverable_count':len(evidence.get('missing_deliverables', []))},
        details_omitted=True)
    if blocked:
        value['stage']['blocked'] = str(stage.get('blocked') or '')[:300]
        value['instruction'] = 'Read project_plan and reopen the blocked stage only with a concrete revised approach. Preserve source, tests and fresh evidence.'
        if evidence_ready:
            value['after_reopen_next_call'] = start_call
            value['instruction'] += ' After reopening, explicitly start the stage, then review actual assertions and map the exact templates; repeated inventory is not missing acceptance evidence.'
    elif can_map:
        value['instruction'] = 'Fresh validation exists. Query acceptance_plan for current executed IDs, review relevant assertions, then fill each exact template with only the relevant original requirement IDs and save with complete=false and the merge option shown. Empty template IDs are intentional; read project_plan for the original IDs and do not copy all stage IDs into every criterion. Never invent evidence or rerun unchanged passing tests.'
    else:
        value['instruction'] = 'Resolve the current execution-plan blocker. Read project_plan for exact missing files, criteria, original requirement IDs and questions. No completion or passing evidence is inferred.'
    value.update(route)
    if route.get('next_action'):
        value['instruction'] = route['next_action']
    if coverage_mapping_pending(hint):
        value['unmapped_stage_requirement_ids'] = list(evidence['missing_requirement_ids'])
        value['instruction'] += ' Existing links already pass. Add relevant mappings for these missing IDs with acceptance_plan; link-only repair cannot cover new requirements and is withheld.'
    if gaps or hint.get('validation_failure'):
        # The instruction already carries this same route; avoid duplicating a
        # long implementation directive in the bounded generation message.
        value.pop('next_action', None)
    value['missing_deliverables'] = list(missing[:8])
    value['missing_deliverable_count'] = len(missing)
    value['missing_deliverables_truncated'] = len(missing)>8
    if gaps:
        value.update(observable_gaps=[{'plan_item_label':r.get('item_id'),'missing':r.get('missing')} for r in gaps[:3]], observable_gap_count=len(gaps),
                     templates_deferred_until_observables_obtained=True)
        templates.clear()
    if missing:
        templates.clear()
        value['templates_deferred_until_deliverables_exist'] = True
    questions = hint.get('open_questions') or []
    if questions:
        value['open_questions'] = [str(q)[:300] for q in questions[:3]]
        value['open_questions_truncated'] = len(questions)>3 or any(len(str(q))>300 for q in questions[:3])
    value['instruction'] += ' Read full_plan_call for omitted templates; never reconstruct criterion text.'
    value['templates_shown'] = len(templates)
    value['templates_truncated'] = len(templates) < value['template_count']
    while len(json.dumps(value,ensure_ascii=False)) > max_chars and templates:
        templates.pop()
        value['templates_shown'] = len(templates)
        value['templates_truncated'] = True
    while len(json.dumps(value,ensure_ascii=False)) > max_chars and value['missing_deliverables']:
        value['missing_deliverables'].pop()
        value['missing_deliverables_truncated'] = True
    while len(json.dumps(value,ensure_ascii=False)) > max_chars and value.get('open_questions'):
        value['open_questions'].pop()
        value['open_questions_truncated'] = True
    if len(json.dumps(value,ensure_ascii=False)) > max_chars:
        value['stage'].pop('blocked',None)
        value['open_questions'] = []
        value['open_questions_truncated'] = bool(questions)
    if len(json.dumps(value,ensure_ascii=False)) > max_chars:
        if gaps and route.get('next_call') is None and not gate:
            value['instruction'] = 'Implement the listed missing asserted outcomes and relevant tests; validate changed source, then link exact receipts. Do not repeat catalog queries or relink unrelated tests. Preserve criteria and existing tests. Read full_plan_call for details.'
        else:
            value['next_call'] = full_plan_call
            value['instruction'] = 'Read the full project_plan for exact evidence and blockers; do not infer omitted criteria or completion.'
    if missing and not value['missing_deliverables'] and value.get('next_tool') == 'write_file':
        value.update(next_call=full_plan_call,next_tool='project_plan',
            next_action='Read project_plan for exact omitted file paths before generating source.',
            instruction='Missing paths were omitted for size. Read the full plan; do not invent file paths.')
    return focus_evidence_hint(value, hint, max_chars, gate)


def focus_evidence_hint(value, hint, max_chars, gate):
    """Retain one exact failed criterion even when writable templates are deferred."""
    stage=hint.get('stage') or {};evidence=stage.get('evidence') or {}
    rows=[r for r in evidence.get('observable_gaps',[]) if isinstance(r.get('criterion'),str) and r['criterion']]
    if not rows:return value
    first=rows[0]
    focus={k:first.get(k,[]) for k in ('criterion','requirement_ids','missing')}
    focus.update(plan_item_label=first.get('item_id'),link_item_id_source='link_acceptance_evidence.rows[].item_id')
    if hint.get('observable_requirement_sources'):
        focus['required_source_requirements']=hint['observable_requirement_sources']
    value={**value,'evidence_focus':focus}
    actionable=(stage.get('status')=='active' and not gate and evidence.get('validation_current') is True
                and not evidence.get('missing_deliverables') and not hint.get('needs_review')
                and not hint.get('plan_required') and not hint.get('open_question_count') and not hint.get('fresh_proof_candidates_available'))
    if actionable:
        instruction='Work on this exact evidence_focus criterion first. Preserve its text, requirement IDs and tests. plan_item_label is NOT a link key; copy the full opaque item_id only from link_acceptance_evidence rows. '
        if 'persisted-mutation' in focus['missing']:
            instruction+='Create a named integration test of saving/updating the relevant record and assert returned/stored data; fix its submit/backend path if the test fails. Repeating form fills cannot prove saving. '
        elif any(kind in focus['missing'] for kind in ('webp-format','decoded-webp-output')):
            instruction+='Invoke actual conversion through the browser converter and display its output in an IMG data URL, then use browser_project assert target=image_format expected=image/webp. A mocked canvas encoder or a MIME label is not conversion evidence and is not decoded-webp-output. Keep useful named unit tests, but obtain the actual decoded host receipt for UI WebP. '
        elif 'csv-content' in focus['missing']:
            instruction+='Create a named test of actual CSV import/export content and parsed fields, not a GET list or label. '
        else:
            instruction+='Perform the relevant behavior and assert the resulting state; visibility alone is structure. '
        instruction+='Finish implementation/tests and validate FIRST, then repeat only necessary fresh browser checks and link exact receipts. Reuse valid work; move to the next criterion after this one is proved.'
        value.update(instruction=instruction,next_call=None,next_tool=None)
        value.pop('next_action',None)
    if len(json.dumps(value,ensure_ascii=False))<=max_chars:return value
    if not actionable:
        reviewing_candidates = (stage.get('status')=='active' and not gate and evidence.get('validation_current') is True
            and not evidence.get('missing_deliverables') and not hint.get('needs_review') and not hint.get('plan_required')
            and not hint.get('open_question_count') and hint.get('fresh_proof_candidates_available'))
        if not reviewing_candidates:
            value.pop('evidence_focus',None)
            return value
        compact={k:value[k] for k in ('revision','plan_required','needs_review','ready_for_delivery','open_question_count','full_plan_call','next_call','next_tool','after_reopen_next_call') if k in value}
        compact.update(stage={'id':stage.get('id'),'status':stage.get('status')},evidence_focus=focus,
            instruction='Follow next_call for the current phase. Review this exact criterion and its required outcomes against actual candidate assertions before linking; a fresh but unrelated receipt does not prove this criterion. Preserve criteria and valid links. If candidates do not prove it, inspect/implement the missing outcome instead of linking by type alone.')
        if len(json.dumps(compact,ensure_ascii=False))<=max_chars:return compact
        compact['evidence_focus']={k:focus[k] for k in ('plan_item_label','missing')}
        compact.update(focus_requires_full_read=True,next_call={'tool':'project_plan','arguments':{'action':'read'}},next_tool='project_plan',
            instruction='Read project_plan for the exact focus criterion and all its requirement IDs before reviewing or linking candidates; the whole focus cannot fit. Never reconstruct it or attach an unrelated proof.')
        return compact
    # Do not clip a criterion or turn a partial text into writable plan content.
    compact={k:value[k] for k in ('revision','plan_required','needs_review','ready_for_delivery','open_question_count','full_plan_call') if k in value}
    compact.update(stage={'id':stage.get('id'),'status':stage.get('status')},evidence_focus=focus,
        next_call=None,next_tool=None,observable_gap_count=len(evidence.get('observable_gaps',[])),
        instruction='Focus on this exact criterion. Implement/test its missing outcome first; field fills are not saved records and labels are not converted files. Preserve criteria/tests. Validate changed source, then capture fresh behavior evidence and link exact IDs.')
    if len(json.dumps(compact,ensure_ascii=False))<=max_chars:return compact
    compact['evidence_focus']={k:focus[k] for k in ('plan_item_label','missing')}
    compact.update(focus_requires_full_read=True,next_call={'tool':'project_plan','arguments':{'action':'read'}},next_tool='project_plan',
        instruction='The exact focus criterion cannot fit this message. Read project_plan for its whole text and requirement IDs before implementing or mapping; do not reconstruct omitted text.')
    return compact


class WorkPlan:
    def __init__(self, workspace, acceptance):
        self.workspace = workspace
        self.acceptance = acceptance
        self.path = self.safe('work-plan.json')
        self.markdown = self.safe('PROJECT_PLAN.md')

    def safe(self, name):
        path = self.workspace.meta / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.workspace.meta.resolve()):
            raise ValueError('Invalid work plan path')
        return path

    def load(self):
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Work plan exceeds the supported size; preserve it for review')
        value = json.loads(self.path.read_text(encoding='utf8'))
        if value.get('version') != 1 or not isinstance(value.get('stages'), list):
            raise ValueError('Unsupported work plan; preserve it for review')
        return value

    def requirements(self):
        return {row['id']: digest(row['text']) for row in self.acceptance.requirements}

    def artifacts(self, stage):
        result = {}
        for name in stage['deliverables']:
            path = self.workspace.path(name)
            if path.is_file():
                result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def evidence(self, stage, coverage, fresh):
        ids = set(stage['requirement_ids'])
        if stage['evidence_kind'] == 'validation':
            status = self.acceptance.mapping_status(self.workspace.last_validation())
            artifacts = self.artifacts(stage)
            missing = [name for name in stage['deliverables'] if name not in artifacts]
            return {'passed': bool(fresh and status.get('ready_for_final_response') and not missing),
                    'hashes': artifacts, 'missing_deliverables': missing,
                    'kind': 'final_validation_and_acceptance', 'validation_current': fresh,
                    'scope': 'All mandatory checks and links; the final user-facing report is checked separately.'}
        if stage['evidence_kind'] == 'source_review':
            catalog = self.acceptance.catalog or {}
            pending = set(catalog.get('pending_obligation_reviews', []))
            rows = [row for row in self.acceptance.requirements if row['id'] in ids]
            passed = bool(self.acceptance.catalog) and not catalog.get('review_issues') and all(
                row.get('source_requirement_id', row['id']) not in pending for row in rows)
            return {'passed': passed, 'kind': 'source_review', 'requirement_ids': sorted(ids)}
        artifacts = self.artifacts(stage)
        missing = [name for name in stage['deliverables'] if name not in artifacts]
        if stage['evidence_kind'] == 'artifact_review':
            reviewed = stage.get('artifact_review', {})
            passed = bool(artifacts) and len(artifacts) == len(stage['deliverables']) and reviewed.get('hashes') == artifacts
            return {'passed': passed, 'kind': 'model_artifact_review', 'hashes': artifacts,
                    'missing_deliverables': missing,
                    'review_reason': reviewed.get('reason')}
        rows = coverage.get('items', [])
        matches = []
        for criterion in stage['criteria']:
            matches.append([row for row in rows if (row.get('passed') or row.get('missing_tests') == ['host:arabic_response']) and text(row['criterion']) == text(criterion)])
        linked = {key for group in matches for row in group for key in row.get('requirement_ids', [])}
        passed = fresh and all(matches) and ids <= linked and len(artifacts) == len(stage['deliverables'])
        observable_gaps = [{'item_id':row['id'], 'criterion':row['criterion'], 'requirement_ids':row.get('requirement_ids',[]),
                            'missing':row['target_reference_review']['observable_review']['missing']}
                           for row in rows if fresh and not row.get('passed') and
                           text(row['criterion']) in {text(c) for c in stage['criteria']} and
                           row.get('target_reference_review', {}).get('observable_review', {}).get('missing')]
        return {'passed': bool(passed), 'kind': 'fresh_acceptance', 'hashes': artifacts,
                'observable_gaps':observable_gaps,
                'covered_requirement_ids': sorted(ids & linked),
                'missing_requirement_ids': sorted(ids - linked),
                'scope_note': 'Every stage requirement ID must be covered by a passing exact matching criterion; matching criterion text alone is insufficient.',
                'missing_deliverables': missing,
                'unmatched_criteria': [criterion for criterion, group in zip(stage['criteria'], matches) if not group],
                'validation_current': fresh, 'final_report_checked_separately': True,
                'matched_criteria': sum(bool(group) for group in matches), 'criteria_count': len(matches)}

    def sync(self, value):
        current = self.requirements()
        value['scope_changes'] = sorted(key for key in set(current) | set(value['requirements'])
                                        if current.get(key) != value['requirements'].get(key))
        value['needs_review'] = bool(value['scope_changes'])
        report = self.workspace.last_validation()
        coverage = self.acceptance.evaluate(report, allow_partial=True)
        fresh = self.acceptance.fresh(report)
        stages = {stage['id']: stage for stage in value['stages']}
        pending = set(stages)
        while pending:
            ready = [key for key in pending if not set(stages[key]['depends_on']) & pending]
            if not ready:
                raise ValueError('Work plan contains a dependency cycle')
            for key in ready:
                stage = stages[key]
                proof = self.evidence(stage, coverage, fresh)
                previous = stage.get('status')
                dependencies = all(stages[dep].get('status') == 'complete' for dep in stage['depends_on'])
                proof['validation_attempt_current'] = bool(report and report.get('current') and not report.get('validation_outdated') and not report.get('in_progress'))
                stage['evidence'] = proof
                if value['needs_review']:
                    stage['status'] = 'needs_review' if set(stage['requirement_ids']) & set(value['scope_changes']) else previous or 'pending'
                elif stage.get('blocked'):
                    stage['status'] = 'blocked'
                elif stage.get('started') and dependencies and proof['passed']:
                    stage['status'] = 'complete'
                elif stage.get('started') and dependencies:
                    stage['status'] = 'active'
                else:
                    stage['status'] = 'pending'
                if previous == 'complete' and stage['status'] != 'complete':
                    stage['reopened_reason'] = 'Requirement, dependency, artifact, or fresh acceptance evidence changed.'
                pending.remove(key)
        # Scheduling is a host responsibility. Do not make the model repeatedly
        # issue start calls for a dependency whose evidence is already current.
        if not value['needs_review'] and not value['open_questions']:
            for _ in range(len(stages)):
                if any(stage['status'] in {'active', 'blocked'} for stage in value['stages']):
                    break
                ready = next((stage for stage in value['stages'] if stage['status'] == 'pending' and not stage.get('manual_hold')
                              and all(stages[key]['status'] == 'complete' for key in stage['depends_on'])), None)
                if ready is None:
                    break
                ready['started'] = ready.get('started') or time.time()
                ready['status'] = 'complete' if ready['evidence']['passed'] else 'active'
        value['ready_for_delivery'] = bool(value['stages']) and not value['needs_review'] and not value['open_questions'] and all(
            stage['status'] == 'complete' for stage in value['stages'])
        return value

    def write(self, value, old):
        if value != old:
            def public_state(record):
                result = json.loads(json.dumps(record or {}))
                for key in ('revision', 'updated'):
                    result.pop(key, None)
                for stage in result.get('stages', []):
                    stage.pop('retry', None)
                return result
            changed = public_state(value) != public_state(old)
            value['revision'] = (old or {}).get('revision', 0) + int(changed)
            value['updated'] = time.time() if changed else old['updated']
            atomic_json(self.path, value)
        # Regenerate from the canonical document after interrupted projection writes.
        lines = ['# خطة عمل المشروع', '', 'يحدّث Studio هذا الملف من سجل الخطة والأدلة.',
                 'مراجعة المستندات في الخطة ينفذها النموذج. لا تمثل مراجعة بشرية.',
                 '', f"إصدار الخطة {value['revision']}",
                 "مراجعة النطاق مطلوبة " + ('نعم' if value['needs_review'] else 'لا'), '',
                 '## مسار العمل', '',
                 '1. فهم الطلب وتجميع متطلباته من المحادثة والمرفقات.',
                 '2. تحديد البنية والمراحل وتبعياتها ومخرجاتها.',
                 '3. تحديد معايير الاكتمال والأدلة المطلوبة لكل مرحلة.',
                 '4. التنفيذ وتسجيل التقدم والعوائق والخطوة التالية.',
                 '5. مراجعة تغييرات النطاق ثم التحقق النهائي والتسليم.', '',
                 '## مصادر المتطلبات', '']
        for row in self.acceptance.requirements:
            lines.append('- ' + row['id'] + ' · ' + row.get('source_kind', 'request') + ' · ' + row['text'])
        lines.extend(['',
                 '## الافتراضات', '', *[f'- {item}' for item in value['assumptions']],
                 '', '## القرارات والمعلومات الناقصة', '', *[f'- {item}' for item in value['open_questions']]])
        for stage in value['stages']:
            lines.extend(['', '## ' + stage['id'] + ' — ' + stage['title'], '',
                          'الحالة ' + {'pending': 'لم تبدأ', 'active': 'قيد التنفيذ', 'complete': 'مكتملة بالدليل',
                                      'blocked': 'متعثرة', 'needs_review': 'تحتاج مراجعة النطاق'}[stage['status']],
                          'تعتمد على ' + ', '.join(stage['depends_on']),
                          'المتطلبات ' + ', '.join(stage['requirement_ids']),
                          'الأدلة ' + stage['evidence_kind'], '',
                          *[f'- {criterion}' for criterion in stage['criteria']],
                          '', *[f'- المخرج {name}' for name in stage['deliverables']]])
            proof = stage.get('evidence', {})
            lines.extend(['', 'توفر الدليل ' + ('نعم' if proof.get('passed') else 'لا')])
            for item in proof.get('missing_deliverables', []):
                lines.append('- مخرج لم يُحفظ بعد ' + item)
            for item in proof.get('missing_requirement_ids', []):
                lines.append('- متطلب بلا تغطية في معايير المرحلة المطابقة ' + item)
            for item in proof.get('unmatched_criteria', []):
                lines.append('- معيار ينتظر دليلاً مطابقاً ' + item)
            if stage.get('blocked'):
                lines.extend(['', 'سبب التعثر ' + stage['blocked'], 'الخطوة التالية ' + stage.get('next_action', '')])
            if stage.get('reopened_reason'):
                lines.extend(['', 'سبب إعادة الفتح ' + stage['reopened_reason']])
        if value.get('change_reason'):
            lines.extend(['', '## آخر تغيير في الخطة', '', value['change_reason']])
        content = '\n'.join(lines) + '\n'
        if self.markdown.exists() and self.markdown.read_text(encoding='utf8') == content:
            return
        temporary = self.safe('PROJECT_PLAN.md.tmp')
        temporary.write_text(content, encoding='utf8')
        temporary.replace(self.markdown)

    def view(self, value):
        if not value:
            current_ids = list(self.requirements())
            return {'plan_required': True, 'revision': 0, 'path': '.local-ai/PROJECT_PLAN.md',
                    'catalog_revision': (self.acceptance.catalog or {}).get('revision'),
                    'current_requirement_ids': current_ids[:128], 'current_requirement_count': len(current_ids),
                    'current_requirement_ids_truncated': len(current_ids)>128,
                    'next_action': 'Append one small stage using draft.next_requirements, then finalize_draft once coverage is complete.',
                    'draft': self.draft_view()}
        next_call = self.next_call(value)
        return {'next_call': next_call, 'plan_required': False, 'revision': value['revision'], 'path': '.local-ai/PROJECT_PLAN.md',
                'needs_review': value['needs_review'], 'scope_changes': value['scope_changes'],
                'draft': self.draft_view() if value['needs_review'] else None,
                'open_questions': value['open_questions'], 'ready_for_delivery': value['ready_for_delivery'],
                'stages': [{k: stage.get(k) for k in ('id', 'title', 'status', 'depends_on', 'requirement_ids',
                           'deliverables', 'criteria', 'evidence_kind', 'evidence', 'blocked', 'next_action', 'reopened_reason')}
                           for stage in value['stages']]}

    def next_call(self, value):
        if value.get('needs_review'):
            return {'tool': 'project_plan', 'arguments': {'action': 'read'},
                    'reason': 'Revise scope or resolve essential questions before execution.'}
        if value.get('open_questions'):
            return None
        if any(stage['status'] == 'active' for stage in value['stages']):
            return None
        statuses = {stage['id']: stage['status'] for stage in value['stages']}
        ready = next((stage for stage in value['stages'] if stage['status'] == 'pending'
                      and all(statuses[key] == 'complete' for key in stage['depends_on'])), None)
        if ready:
            return {'tool': 'project_plan', 'arguments': {'action': 'start', 'stage_id': ready['id'],
                    'expected_revision': value['revision']},
                    'reason': 'This manually reopened stage requires an explicit start after reviewing its revised approach.'}
        return None

    def read(self):
        with self.workspace.history.lock:
            old = self.load()
            if not old:
                return self.view(None)
            value = self.sync(json.loads(json.dumps(old)))
            self.write(value, old)
            return self.view(value)

    def behavior_ids(self):
        return {row['id'] for row in self.acceptance.requirements if row.get('evidence_kind') == 'behavior'}

    def implementation_missing(self, stages):
        covered = {key for row in stages if row['evidence_kind'] == 'acceptance' for key in row['requirement_ids']}
        return self.behavior_ids() - covered

    def load_draft(self):
        path = self.safe('work-plan-draft.json')
        old = self.load()
        seed = {'revision': 0, 'requirements': self.requirements(), 'stages': [], 'assumptions': [], 'open_questions': []}
        if old:
            seed.update(stages=[{k: row[k] for k in STAGE['required']} for row in old['stages']],
                        assumptions=old['assumptions'], open_questions=old['open_questions'])
            seed['base_plan'] = digest({k: seed[k] for k in ('stages','assumptions','open_questions')})
        if not path.exists():
            return seed
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Planning draft exceeds supported size; preserve it for review')
        draft = json.loads(path.read_text(encoding='utf8'))
        if old:
            if draft.get('published') or draft['stages'] == seed['stages']:
                return seed
            if draft.get('base_plan') != seed['base_plan']:
                raise ValueError('Published plan changed while drafting. Preserve both plans and review before merging.')
        return draft

    def draft_view(self):
        draft = self.load_draft()
        covered = {key for stage in draft['stages'] for key in stage['requirement_ids']}
        behavior_missing = self.implementation_missing(draft['stages'])
        missing = [row for row in self.acceptance.requirements if row['id'] not in covered or row['id'] in behavior_missing]
        stale = draft['requirements'] != self.requirements()
        return {'revision': draft['revision'], 'stale': stale, 'stage_count': len(draft['stages']),
                'saved_stages': draft['stages'], 'missing_count': len(missing), 'implementation_missing_count': len(behavior_missing),
                'next_requirements': [{'id': row['id'], 'text': row['text'], 'evidence_kind': row.get('evidence_kind')} for row in missing[:16]],
                'next_action': 'review_changed_scope' if stale else 'append_stages' if missing else 'finalize_draft',
                'instruction': 'Save ONE coherent small stage covering up to 16 shown IDs. Depend only on saved stage IDs. Do not resend earlier stages. Behavior requirements MUST appear in acceptance stages; source_review is not implementation evidence. No implementation until finalize_draft succeeds.'}

    def draft_action(self, action, stages, assumptions, questions, reason, expected):
        if (self.acceptance.catalog or {}).get('review_issues'):
            raise ValueError('Review original sources before drafting the plan')
        draft = self.load_draft()
        if draft['requirements'] != self.requirements():
            raise ValueError('Requirement scope changed during drafting. Preserve the draft and review the new scope before saving a complete revised plan.')
        if expected is not None and (type(expected) is not int or expected != draft['revision']):
            raise ValueError('Draft revision changed; read project_plan for the current draft revision')
        if action == 'finalize_draft':
            if stages is not None or assumptions is not None or questions is not None:
                raise ValueError('finalize_draft uses saved stages and context; do not replace them')
            result = self.invoke(action='save', expected_revision=(self.load() or {}).get('revision', 0), stages=draft['stages'], assumptions=draft['assumptions'],
                               open_questions=draft['open_questions'], reason=reason or 'Add missing implementation coverage while preserving saved stages and criteria')
            draft['published'] = True
            atomic_json(self.safe('work-plan-draft.json'), draft)
            return result
        if not isinstance(stages, list) or len(stages) != 1:
            raise ValueError('append_stages requires exactly ONE small stage')
        if not isinstance(stages[0], dict) or not isinstance(stages[0].get('requirement_ids'), list) or len(stages[0]['requirement_ids']) > 16:
            raise ValueError('A draft batch covers at most 16 requirement IDs')
        for rows, limit in ((assumptions, 30), (questions, 20)):
            if rows is not None and (not isinstance(rows, list) or len(rows) > limit or not all(isinstance(row, str) and 1 <= len(row) <= 1000 for row in rows)):
                raise ValueError('Questions and assumptions must be bounded text lists')
        existing = next((row for row in draft['stages'] if row['id'] == stages[0].get('id')), None)
        if existing:
            if existing == stages[0] and assumptions is None and questions is None:
                return {**self.view(None), 'unchanged': True}
            raise ValueError('Draft stages are append-only; preserve saved IDs and criteria')
        combined = draft['stages'] + stages
        self.validate_stages(combined, partial=True)
        covered = {key for row in draft['stages'] for key in row['requirement_ids']}
        if set(stages[0]['requirement_ids']) & self.implementation_missing(draft['stages']) and stages[0]['evidence_kind'] != 'acceptance':
            raise ValueError('Use evidence_kind=acceptance for missing behavior implementation; source review alone cannot cover it')
        if not set(stages[0]['requirement_ids']) - covered and not (set(stages[0]['requirement_ids']) & self.implementation_missing(draft['stages']) and stages[0]['evidence_kind'] == 'acceptance'):
            raise ValueError('This batch adds no requirement coverage; use next uncovered IDs')
        merged = list(dict.fromkeys(draft['assumptions'] + (assumptions or [])))
        if len(merged) > 30:
            raise ValueError('The combined assumption list exceeds 30 entries')
        if questions is not None and not set(draft['open_questions']) <= set(questions):
            raise ValueError('Preserve draft questions; resolve them after publishing with update_context')
        draft.update(stages=combined, assumptions=merged,
                     open_questions=draft['open_questions'] if questions is None else questions,
                     revision=draft['revision'] + 1)
        prior = self.safe('work-plan-draft.json')
        if prior.exists():
            self.safe('work-plan-draft-history').mkdir(exist_ok=True)
            previous = json.loads(prior.read_text(encoding='utf8'))
            atomic_json(self.safe('work-plan-draft-history/' + digest(previous) + '.json'), previous)
        atomic_json(prior, draft)
        return self.view(None)

    def validate_stages(self, stages, *, partial=False):
        if not isinstance(stages, list) or not 1 <= len(stages) <= 40:
            raise ValueError('Save 1 to 40 stages')
        ids = [stage.get('id') for stage in stages if isinstance(stage, dict)]
        if len(ids) != len(stages) or len(set(ids)) != len(ids) or any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', key) for key in ids):
            raise ValueError('Stages require unique short stable IDs such as S1, S2')
        current_ids = list(self.requirements())
        known = set(current_ids)
        invalid = {}
        for stage in stages:
            references = stage.get('requirement_ids')
            if isinstance(references, list) and all(isinstance(key, str) for key in references):
                unknown = sorted(set(references) - known)
                if unknown:
                    invalid[stage['id']] = unknown
        if invalid:
            raise PlanValidationError(invalid, current_ids)
        covered = set()
        for stage in stages:
            if set(stage) != set(STAGE['required']) or stage['evidence_kind'] not in EVIDENCE_KINDS:
                raise ValueError('Provide only the documented stage fields and evidence kind')
            if not isinstance(stage['title'], str) or not 3 <= len(stage['title'].strip()) <= 200:
                raise ValueError('Use a concrete stage title')
            for field in ('depends_on', 'requirement_ids', 'deliverables', 'criteria'):
                rows = stage[field]
                limits = {'depends_on': 40, 'requirement_ids': 256, 'deliverables': 20, 'criteria': 12}
                if (not isinstance(rows, list) or len(rows) > limits[field]
                        or not all(isinstance(row, str) and row for row in rows) or len(set(rows)) != len(rows)):
                    error = ValueError('Use unique nonempty strings for ' + field)
                    duplicates = [[i for i, entry in enumerate(rows) if entry == row] for row in dict.fromkeys(rows)] if isinstance(rows, list) and all(isinstance(row,str) for row in rows) else []
                    error.plan_repair = {'batch_saved': False, 'stage_id': stage['id'], 'field': field,
                                         'duplicate_positions': [group for group in duplicates if len(group)>1],
                                         'max_items': limits[field],
                                         'next_action': 'Correct only the listed field. Keep one copy of each EXACT repeated string; preserve every distinct criterion and requirement. Use next uncovered IDs from draft. No plan or criterion was saved.'}
                    raise error
            if not stage['requirement_ids'] or not set(stage['requirement_ids']) <= known:
                raise ValueError('Use current requirement IDs for every stage')
            if not stage['criteria'] or any(not 5 <= len(item) <= 2000 for item in stage['criteria']):
                raise ValueError('Every stage needs concrete completion criteria')
            if not set(stage['depends_on']) <= set(ids) or stage['id'] in stage['depends_on']:
                raise ValueError('Stage dependencies must reference other stage IDs')
            if stage['evidence_kind'] == 'artifact_review' and not stage['deliverables']:
                raise ValueError('An artifact review requires actual deliverable files')
            if stage['evidence_kind'] == 'acceptance' and not stage['deliverables']:
                raise ValueError('An implementation acceptance stage requires concrete deliverable file paths before evidence mapping')
            for path in stage['deliverables']:
                if path.replace('\\', '/').startswith('.local-ai/'):
                    raise ValueError('The host generates .local-ai/PROJECT_PLAN.md automatically. '
                                     'Do not list runtime metadata as stage deliverables. '
                                     'Use [] for source_review, or a real document such as design.md for artifact_review.')
                if stage['evidence_kind'] == 'artifact_review' and Path(path).suffix.lower() not in {'.md', '.txt', '.rst', '.adoc', '.mmd', '.puml', '.drawio'}:
                    raise ValueError('artifact_review is only for design documents. Use acceptance for executable code and validation for the final all-requirements check.')
                self.workspace.path(path)
                suffix = Path(path).suffix
                if any(character.isspace() for character in suffix) or not suffix and any(character.isspace() for character in Path(path).name):
                    raise ValueError('deliverables must be exact file paths, never descriptions or command receipts. Use app.py, design.md, or [] for a source-review stage.')
            covered.update(stage['requirement_ids'])
        if not partial and covered != known:
            raise ValueError('Plan must cover every current requirement ID; missing ' + ', '.join(sorted(known - covered)))
        if not partial and self.implementation_missing(stages):
            raise ValueError('Behavior requirements need acceptance-stage implementation coverage, not only source review; missing ' + ', '.join(sorted(self.implementation_missing(stages))))
        # Validate the entire graph before writing.
        remaining = {stage['id']: set(stage['depends_on']) for stage in stages}
        while remaining:
            ready = {key for key, deps in remaining.items() if not deps & set(remaining)}
            if not ready:
                raise ValueError('Stage dependencies contain a cycle')
            remaining = {key: deps for key, deps in remaining.items() if key not in ready}

    def _archive(self, old):
        archive = self.safe('work-plan-history')
        archive.mkdir(exist_ok=True)
        atomic_json(self.safe('work-plan-history/' + digest(old) + '.json'), old)

    def _update_context(self, old, assumptions, open_questions, reason, stages, stage_id):
        if not old:
            raise ValueError('Save the project plan before updating its context')
        if stages is not None or stage_id is not None:
            raise ValueError('update_context cannot change stages or criteria: omit stage_id/stages and provide reason plus assumptions or open_questions. For a stage obstacle use block(stage_id, reason, next_action); repair source with read_file then patch_file.')
        if assumptions is None and open_questions is None:
            raise ValueError('Provide documented assumptions or the remaining unresolved question list')
        if not isinstance(reason, str) or len(reason.strip()) < 10:
            raise ValueError('update_context requires a concrete decision or resolution reason')
        for rows, limit in ((assumptions, 30), (open_questions, 20)):
            if rows is not None and (not isinstance(rows, list) or len(rows) > limit
                    or not all(isinstance(row, str) and 1 <= len(row) <= 1000 for row in rows)):
                raise ValueError('Questions and assumptions must be bounded text lists')
        value = self.sync(json.loads(json.dumps(old)))
        if value['needs_review']:
            raise ValueError('Review the changed requirement scope before updating plan context')
        combined = list(dict.fromkeys([*value['assumptions'], *(assumptions or [])]))
        if len(combined) > 30:
            raise ValueError('The combined assumption list exceeds 30 entries')
        questions = value['open_questions'] if open_questions is None else open_questions
        changed = combined != value['assumptions'] or questions != value['open_questions']
        if changed:
            self._archive(old)
            value.update(assumptions=combined, open_questions=list(questions), change_reason=reason)
        value = self.sync(value)
        self.write(value, old)
        result = self.view(value)
        if not changed:
            result['unchanged'] = value['revision'] == old['revision']
        return result

    def invoke(self, action='read', expected_revision=None, stages=None, assumptions=None, open_questions=None,
               stage_id=None, reason='', next_action='', expected_draft_revision=None):
        with self.workspace.history.lock:
            old = self.load()
            if action == 'read':
                value = self.read()
                if stage_id is not None:
                    selected = next((row for row in value.get('stages',[]) if row['id'] == stage_id),None)
                    if selected is None:
                        raise ValueError('Use a current stage_id for a scoped plan read')
                    ids = set(selected['requirement_ids'])
                    return {'revision':value.get('revision'),'path':'.local-ai/PROJECT_PLAN.md',
                            'stage':selected,'stage_requirements':[{key:row[key] for key in ('id','text','evidence_kind','source_id') if key in row} for row in self.acceptance.requirements if row['id'] in ids],
                            'scope':'Selected stage only; all other stages, requirements and criteria are preserved.',
                            'next_action':'Implement and test the exact unmet stage outcomes before mapping. Existence of its files or a passing unrelated suite does not prove its behavior. Select only relevant stage requirement IDs for each exact criterion; never copy every ID to each criterion.'}
                return value
            if action in {'append_stages', 'finalize_draft'}:
                return self.draft_action(action, stages, assumptions, open_questions, reason, expected_draft_revision)
            if not old and action == 'save' and expected_revision is None:
                expected_revision = 0
            if expected_revision != (old or {}).get('revision', 0):
                raise ValueError('Plan revision changed; read project_plan and use its current revision')
            if action == 'update_context':
                return self._update_context(old, assumptions, open_questions, reason, stages, stage_id)
            if action == 'update_stage':
                if not old or not isinstance(stages, list) or len(stages) != 1 or not isinstance(stages[0], dict):
                    error = ValueError('update_stage requires stages=[ONE complete current stage]; stage_id and next_action alone cannot change execution evidence')
                    selected = next((row for row in (old or {}).get('stages', []) if row['id'] == stage_id), None)
                    error.plan_repair = {'batch_saved': False, 'next_action': 'If changing the graph is necessary, use stages=[the complete current stage] and a concrete reason; preserve all criteria. For missing execution evidence follow the current host validation/acceptance action instead of editing stage status.',
                                         'update_stage_template': {'action':'update_stage','expected_revision':(old or {}).get('revision',0),'stages':[{k:selected[k] for k in STAGE['required']}] if selected else [],'reason':'Describe an actual scope or criterion change'}}
                    raise error
                selected = stages[0]
                if selected.get('id') not in {row['id'] for row in old['stages']}:
                    raise ValueError('update_stage requires an existing stage ID; read project_plan')
                before = next(row for row in old['stages'] if row['id'] == selected['id'])
                for field in ('criteria', 'requirement_ids', 'depends_on', 'deliverables'):
                    if not isinstance(selected.get(field), list) or not set(before[field]) <= set(selected[field]):
                        raise ValueError('update_stage must preserve existing ' + field)
                if selected.get('evidence_kind') != before['evidence_kind']:
                    raise ValueError('update_stage cannot change the evidence kind')
                if assumptions is not None or open_questions is not None:
                    raise ValueError('Use update_context separately to change questions or assumptions')
                combined = [selected if row['id'] == selected['id'] else {k: row[k] for k in STAGE['required']} for row in old['stages']]
                return self.invoke(action='save', expected_revision=expected_revision, stages=combined,
                                   assumptions=old['assumptions'], open_questions=old['open_questions'], reason=reason)
            if action == 'save':
                if old and isinstance(stages, list) and {row.get('id') for row in stages if isinstance(row, dict)} < {row['id'] for row in old['stages']}:
                    error = ValueError('save requires the entire graph; use update_stage to revise ONE existing stage')
                    error.plan_repair = {'batch_saved': False, 'next_action': 'Call project_plan action=update_stage with ONE complete existing stage, preserve its current criteria and IDs, and give a concrete reason. The host retains all other stages and validates the full graph. Do not regenerate the whole plan.', 'current_stage_ids': [row['id'] for row in old['stages']]}
                    raise error
                self.validate_stages(stages)
                pending_draft = self.load_draft() if self.safe('work-plan-draft.json').exists() else None
                if not old or (pending_draft and pending_draft.get('base_plan') and pending_draft['stages'] != [{k: row[k] for k in STAGE['required']} for row in old['stages']]):
                    draft = pending_draft or self.load_draft()
                    if draft['requirements'] == self.requirements():
                        proposed = {stage['id']: stage for stage in stages}
                        if any(proposed.get(stage['id']) != stage for stage in draft['stages']):
                            raise ValueError('Preserve every saved draft stage, criterion, deliverable and dependency')
                        if not set(draft['assumptions']) <= set(assumptions or []) or not set(draft['open_questions']) <= set(open_questions or []):
                            raise ValueError('Preserve draft assumptions and unresolved questions when publishing')
                if old:
                    changed_scope = {key for key in set(self.requirements()) | set(old['requirements'])
                                     if self.requirements().get(key) != old['requirements'].get(key)}
                    proposed = {(text(criterion), tuple(sorted(stage['requirement_ids'])))
                                for stage in stages for criterion in stage['criteria']}
                    lost = [criterion for stage in old['stages']
                            if not changed_scope.intersection(stage['requirement_ids'])
                            for criterion in stage['criteria']
                            if (text(criterion), tuple(sorted(stage['requirement_ids']))) not in proposed]
                    if lost:
                        raise ValueError('Do not remove existing stage criteria for unchanged requirements. Preserve them and add stronger criteria, or review an actual user scope change: ' + json.dumps(lost, ensure_ascii=False))
                for rows, limit in ((assumptions, 30), (open_questions, 20)):
                    if rows is not None and (not isinstance(rows, list) or len(rows) > limit
                            or not all(isinstance(row, str) and 1 <= len(row) <= 1000 for row in rows)):
                        raise ValueError('Questions and assumptions must be bounded text lists')
                if old and (not isinstance(reason, str) or len(reason.strip()) < 10):
                    raise ValueError('A revised plan needs a concrete change reason')
                value = {'version': 1, 'revision': 0, 'requirements': self.requirements(),
                         'assumptions': assumptions or [], 'open_questions': open_questions or [],
                         'stages': [], 'change_reason': reason}
                previous = {stage['id']: stage for stage in (old or {}).get('stages', [])}
                for stage in stages:
                    record = json.loads(json.dumps(stage))
                    before = previous.get(stage['id'])
                    if before and all(before[k] == stage[k] for k in STAGE['required']):
                        record = json.loads(json.dumps(before))
                    value['stages'].append(record)
                if old:
                    self._archive(old)
            else:
                if not old:
                    raise ValueError('Save the plan first')
                value = json.loads(json.dumps(old))
                self.sync(value)
                if value['needs_review'] or value['open_questions']:
                    raise ValueError('Revise changed scope or resolve the essential questions before execution')
                selected = next((stage for stage in value['stages'] if stage['id'] == stage_id), None)
                if not selected:
                    raise ValueError('Use a current stage_id')
                if action == 'start':
                    if selected['status'] == 'complete':
                        return self.view(value)
                    if selected.get('blocked'):
                        raise ValueError('Reopen this blocked stage with a concrete new approach')
                    if any(stage['id'] in selected['depends_on'] and stage['status'] != 'complete' for stage in value['stages']):
                        raise ValueError('Complete the dependency stages first')
                    if any(stage['status'] == 'active' and stage['id'] != stage_id for stage in value['stages']):
                        raise ValueError('Finish or block the active stage first')
                    selected.pop('manual_hold', None)
                    selected['started'] = selected.get('started') or time.time()
                elif action in {'block', 'reopen', 'review_artifacts'}:
                    if not isinstance(reason, str) or len(reason.strip()) < 10:
                        raise ValueError('State the specific obstacle, changed evidence, or revised approach')
                    if action == 'block':
                        if not isinstance(next_action, str) or len(next_action.strip()) < 5:
                            raise ValueError('A blocked stage requires a concrete next action')
                        selected.update(blocked=reason, next_action=next_action)
                    elif action == 'reopen':
                        selected.pop('blocked', None)
                        selected.pop('retry', None)
                        selected.pop('artifact_review', None)
                        selected['started'] = None
                        selected['manual_hold'] = True
                        selected['reopened_reason'] = reason
                    else:
                        if selected['evidence_kind'] != 'artifact_review' or selected['status'] != 'active':
                            raise ValueError('Review artifacts only for an active document stage')
                        hashes = self.artifacts(selected)
                        if len(hashes) != len(selected['deliverables']):
                            raise ValueError('Save every declared deliverable before reviewing it')
                        selected['artifact_review'] = {'hashes': hashes, 'reason': reason, 'reviewer': 'model'}
                else:
                    raise ValueError('Unknown project_plan action')
            value = self.sync(value)
            self.write(value, old)
            return self.view(value)

    def gate(self):
        value = self.read()
        if (self.acceptance.catalog or {}).get('review_issues'):
            from project_requirement_lifecycle import source_review_batch
            batch = source_review_batch(self.acceptance.catalog)
            return {'error': 'Review original requirement sources before implementation.',
                    'next_tool': 'review_project_requirements',
                    'catalog_revision': batch['catalog_revision'], 'next_source_batch': batch,
                    'reason': 'This is a requirements review gate, not a source-code failure.'}
        if not value.get('plan_required') and not value.get('needs_review') and self.implementation_missing(value['stages']):
            return {'error': 'Published plan lacks behavior implementation stages.', 'next_tool': 'project_plan',
                    'next_action': 'Append small acceptance stages for missing behavior coverage, preserving all existing stages and criteria, then finalize_draft.',
                    'draft': self.draft_view()}
        if value.get('plan_required') or value.get('needs_review') or value.get('open_questions'):
            current_ids = list(self.requirements())
            questions = value.get('open_questions', [])
            return {'error': 'Resolve the recorded open questions before execution.' if questions else
                            'Project plan is required or needs scope review before implementation.',
                    'current_revision': value.get('revision', 0),
                    'open_questions': questions[:3], 'open_question_count': len(questions),
                    'open_questions_truncated': len(questions)>3,
                    'next_action': 'If a question already contains its answer, document the decision in assumptions '
                        'and remove it from open_questions using update_context with a concrete reason. '
                        'Preserve genuinely unanswered essential questions and use ask_user for missing input. '
                        'Do not repeatedly start stages or resave an unchanged graph.' if questions else
                        'Append ONE small stage using draft.next_requirements. Finalize only when missing_count is zero.' if value.get('plan_required') else 'Revise the plan preserving all current requirements.',
                    'draft': value.get('draft'),
                    'catalog_revision': (self.acceptance.catalog or {}).get('revision'),
                    'current_requirement_ids': current_ids[:128], 'current_requirement_count': len(current_ids),
                    'current_requirement_ids_truncated': len(current_ids)>128,
                    'next_tool': 'project_plan', 'next_call': value.get('next_call'), 'project_plan': value}
        if not any(stage['status'] == 'active' for stage in value['stages']):
            return {'error': 'Start a ready project stage before implementation, or resolve its recorded obstacle.',
                    'next_tool': 'project_plan', 'next_call': value.get('next_call'), 'project_plan': value}
        return None

    def hint(self):
        value = self.read()
        if value.get('plan_required'):
            return value
        selected = next((stage for stage in value['stages'] if stage['status'] in {'active', 'blocked'}), None)
        if selected is None:
            selected = next((stage for stage in value['stages'] if stage['status'] != 'complete'), None)
        from project_acceptance_targets import observable_sources
        from project_generation import diagnostic_excerpt
        report = self.workspace.last_validation()
        failure = None
        if report and report.get('success') is False and report.get('current'):
            failed = [row for row in report.get('checks',[]) if row.get('passed') is not True]
            failure = {'success':False, 'failure_count':len(failed),
                'failures':[{'name':str(row.get('name',''))[:100],
                    'error':str(row.get('error',''))[:160],
                    'diagnostic':diagnostic_excerpt((row.get('stdout') or '')+'\n'+(row.get('stderr') or ''),1200)[:800]}
                    for row in failed[:1]],
                'full_report':'read_continuation.validation',
                'note':'First actual executed failure; a static diagnostics pass cannot clear it. Full report remains preserved.'}
        first_gap = next(iter((selected or {}).get('evidence',{}).get('observable_gaps',[])),{})
        sources = observable_sources(self.acceptance.requirements, first_gap)
        return {'validation_failure':failure, 'observable_requirement_sources':sources,'revision': value['revision'], 'needs_review': value['needs_review'],
                'open_questions': value.get('open_questions', [])[:3],
                'open_question_count': len(value.get('open_questions', [])),
                'ready_for_delivery': value['ready_for_delivery'], 'next_call': value.get('next_call'),
                'acceptance_templates': [{'requirement_ids': [], 'criterion': criterion,
                    'tests': []} for criterion in selected['evidence'].get('unmatched_criteria', [])] if selected and selected['evidence_kind'] == 'acceptance' else [],
                'acceptance_write_options': {'merge': bool(self.acceptance.load()), 'complete': False},
                'stage': {key: selected.get(key) for key in ('id', 'status', 'requirement_ids', 'deliverables', 'criteria', 'evidence', 'blocked', 'next_action')}
                         if selected else None,
                'instruction': 'Fill each template requirement_ids only with original IDs actually covered by that criterion, and tests with relevant exact receipts after reviewing their assertions. Do not copy all stage IDs into every criterion. Empty IDs are intentional and must be selected from the stage requirements before saving acceptance_plan(items=templates, **acceptance_write_options). Preserve exact criterion text and existing valid links. The host activates the next dependency-ready stage automatically. Cover all stage requirement IDs across relevant criteria, not in every criterion; use merge=true to retain existing valid mappings. Use exact file paths as deliverables. Acceptance criterion texts must match these plan criteria. Read project_plan for the full graph. Source-review completion uses the reviewed requirement ledger.'}

    def observe(self, name, arguments, result):
        with self.workspace.history.lock:
            old = self.load()
            if not old:
                return False
            value = self.sync(json.loads(json.dumps(old)))
            if name in {'review_project_requirements', 'review_atomic_requirement'} and result.get('saved') and result.get('resolved_pending_source_reviews') and not result.get('error'):
                # A committed source review is real preparation progress even
                # when requirement IDs, executable files and test receipts stay unchanged.
                self._archive(old)
                for stage in value['stages']:
                    if stage.get('status') == 'active' or stage.get('blocked') == 'Repeated operation or rejected attempts on unchanged stage evidence.':
                        stage.pop('retry', None)
                        if stage.get('blocked') == 'Repeated operation or rejected attempts on unchanged stage evidence.':
                            stage.pop('blocked', None)
                            stage.pop('next_action', None)
                            stage['reopened_reason'] = 'A pending original-source review was committed; continue with its preserved obligations and current evidence.'
                self.write(self.sync(value), old)
                return False
            active = next((stage for stage in value['stages'] if stage['status'] == 'active'), None)
            if not active:
                self.write(value, old)
                return False
            signature = digest([self.workspace.fingerprint(force=True), active['evidence'],
                                value['requirements'], active.get('artifact_review')])
            retry = active.setdefault('retry', {'signature': signature, 'actions': {}, 'failures': 0})
            if retry['signature'] != signature:
                retry = active['retry'] = {'signature': signature, 'actions': {}, 'failures': 0}
            action_scope = {'stage_id':active['id'],'criteria':active['criteria'],
                            'requirement_ids':active['requirement_ids']} if name in {'acceptance_plan','link_acceptance_evidence'} else None
            action = digest([name, arguments, action_scope]) if action_scope else digest([name, arguments])
            if action not in retry['actions'] and len(retry['actions']) >= 256:
                retry['actions'].pop(next(iter(retry['actions'])))
            retry['actions'][action] = retry['actions'].get(action, 0) + 1
            if result.get('error'):
                retry['failures'] += 1
            blocked = retry['actions'][action] >= 4 or retry['failures'] >= 8
            if blocked:
                active.update(blocked='Repeated operation or rejected attempts on unchanged stage evidence.',
                              next_action='Review the obstacle and reopen with a specific revised approach.')
            value = self.sync(value)
            self.write(value, old)
            return blocked


def collection_required(hint, page):
    stage = hint.get('stage') or {}
    evidence = stage.get('evidence') or {}
    gaps = evidence.get('observable_gaps') or []
    if stage.get('status') != 'active' or not evidence.get('validation_current') or evidence.get('missing_deliverables') or not gaps:
        return False
    ui_gaps = [row for row in gaps if any(value.startswith('ui-') or value in {'webp-format','decoded-webp-output','persisted-mutation'} for value in row.get('missing',[]))]
    if not ui_gaps:
        return False
    proofs = page.get('current_browser_evidence') or []
    required = set(ui_gaps[0].get('missing', []))
    def covers(proof, kind):
        interactive = (proof.get('source') in {'text','value','checked','count','dialog_count','image_format'}
                       and proof.get('action') in {'click','fill','select','press','upload'}
                       and proof.get('state_changed') is True)
        if kind == 'ui-interaction':
            return interactive
        if kind == 'persisted-mutation':
            return interactive and proof.get('mutation_observed') is True
        if kind in {'webp-format','decoded-webp-output'}:
            value = proof.get('image_format') or {}
            return value.get('mime') == 'image/webp' and value.get('decoded') is True
        return kind == 'ui-observation'
    if proofs and all(any(covers(proof, kind) for proof in proofs) for kind in required):
        return False
    ids = {row['item_id'] for row in ui_gaps}
    relevant = [row for row in page.get('rows',[]) if row['item_id'].split(':')[0] in ids]
    return not any(row.get('candidate_successful_tests') or row.get('automatic_replacements') for row in relevant)


def collection_tools(tools, required):
    return [entry for entry in tools if entry['function']['name'] not in {'acceptance_plan','link_acceptance_evidence'}] if required else tools


def routine_evidence_phase(hint):
    stage = hint.get('stage') or {}
    proof = stage.get('evidence') or {}
    return bool(stage.get('status') in {'active','pending','blocked'} and proof.get('validation_current')
                and proof.get('observable_gaps') and not hint.get('open_question_count')
                and not hint.get('open_questions') and not hint.get('needs_review'))


def delivery_final_allowed(read_only, validation, hint, collecting=False):
    if read_only:
        return True
    if collecting or not validation.get('success') or not validation.get('current'):
        return False
    return not hint or ('ready_for_delivery' in hint and hint['ready_for_delivery'] is True)


def focused_evidence_tools(tools, hint):
    """Existing criteria need observable proof, not a replacement stage graph."""
    if not routine_evidence_phase(hint):
        return tools
    import copy
    allowed = {'read_file','write_file','patch_file','delete_file','validate_project','browser_project','link_acceptance_evidence','project_plan','ask_user','run_project','stop_project'}
    result = copy.deepcopy([tool for tool in tools if tool['function']['name'] in allowed])
    status = hint['stage']['status']
    actions = ['read','update_context'] + (['reopen'] if status == 'blocked' else ['start'] if status == 'pending' else ['block'])
    for tool in result:
        if tool['function']['name'] == 'project_plan':
            tool['function']['description'] = 'Existing plan graph is preserved during evidence collection/linking. Allowed actions: '+', '.join(actions)+'. Use stage_id for start/reopen/block, with a concrete reason when required. update_context records essential decisions/questions only; never resend stages or criteria. Use link_acceptance_evidence to repair existing acceptance links.'
            fields = tool['function']['parameters']['properties']
            tool['function']['parameters']['properties'] = {key:value for key,value in fields.items() if key not in {'stages','expected_draft_revision'}}
            tool['function']['parameters']['properties']['action']['enum'] = actions
    return result


def fresh_candidates_ready(hint, page):
    """Assess proof collection independently of the stage's scheduling lock."""
    stage = hint.get('stage') or {}; evidence = stage.get('evidence') or {}
    if (stage.get('status') not in {'active','pending','blocked'} or not evidence.get('validation_current')
            or not evidence.get('observable_gaps') or evidence.get('missing_deliverables')
            or hint.get('needs_review') or hint.get('plan_required') or hint.get('open_question_count')):
        return False
    probe = {**hint,'stage':{**stage,'status':'active'}}
    ids = {row['item_id'] for row in evidence['observable_gaps']}
    relevant = [row for row in page.get('rows',[]) if row['item_id'].split(':')[0] in ids]
    return bool(not collection_required(probe,page) and (page.get('current_browser_evidence')
                or any(row.get('candidate_successful_tests') or row.get('automatic_replacements') for row in relevant)))


def coverage_mapping_pending(hint):
    """Passing existing links cannot add an unmapped original requirement."""
    stage = hint.get('stage') or {}
    proof = stage.get('evidence') or {}
    return bool(stage.get('status') == 'active' and proof.get('validation_current') is True
        and proof.get('missing_requirement_ids') and not proof.get('unmatched_criteria')
        and not proof.get('observable_gaps') and not proof.get('missing_deliverables')
        and not hint.get('needs_review') and not hint.get('open_question_count'))


def stage_mapping_scope(hint):
    stage = hint.get('stage') or {}; evidence = stage.get('evidence') or {}
    if (stage.get('status') != 'active' or evidence.get('kind') != 'fresh_acceptance'
            or not stage.get('criteria') or not stage.get('requirement_ids')
            or hint.get('needs_review') or hint.get('open_question_count')):
        return None
    missing = evidence.get('unmatched_criteria') or []
    uncovered = coverage_mapping_pending(hint)
    ids = evidence['missing_requirement_ids'] if uncovered else stage['requirement_ids']
    return {'stage_id':stage['id'],'requirement_ids':list(ids),
            'criterion_focus':next(iter(missing or stage['criteria'])),
            'criterion_count':len(stage['criteria']),
            'read_call':{'tool':'project_plan','arguments':{'action':'read','stage_id':stage['id']}},
            'instruction':('Existing stage criteria already have passing links, but these original IDs are still unmapped: '+json.dumps(ids)+'. Use acceptance_plan with complete=false and merge=true to add relevant coverage; link_acceptance_evidence cannot add requirement IDs and is unavailable for this state. Preserve every existing row. ' if uncovered else '')+'Work on this active stage only. Read its exact original requirements and criteria using read_call. Implement missing behavior and meaningful assertions first; file existence or unrelated passing tests are insufficient. Choose only relevant requirement IDs for each exact stage criterion. Do not resubmit a completed earlier-stage row, invent receipts, or weaken requirements.'}


def guard_stage_mapping(hint, items, options=None):
    missing = ((hint.get('stage') or {}).get('evidence') or {}).get('missing_deliverables') or []
    if items and missing:
        return {'error': 'Implement the declared missing deliverables before acceptance mapping.',
                'not_executed': True, 'missing_deliverables': missing, 'next_tool': 'write_file',
                'next_action': 'Create the missing implementation and meaningful tests, validate them, then map only relevant passing evidence. Existing unrelated tests cannot certify this stage.'}
    scope = stage_mapping_scope(hint)
    if scope and options and options.get('repair') is not True and any(key in options for key in ('repair','repairs','plan_revision')):
        return {'error':'Legacy link-repair arguments are unavailable in acceptance_plan during active-stage mapping. No acceptance row or link changed.',
                'not_executed':True,'stage_mapping_scope':scope,'next_tool':'project_plan','next_call':scope['read_call'],
                'next_action':scope['instruction']+' Use link_acceptance_evidence only for an actual unresolved existing link, using its current exact IDs. Do not copy old examples or repair an already satisfied earlier-stage row.'}
    if not scope or not isinstance(items,list) or not items:
        return None
    stage = hint['stage']; ids = set(scope['requirement_ids']); criteria = set(stage['criteria'])
    invalid = [i for i,row in enumerate(items) if isinstance(row,dict) and
               (row.get('criterion') not in criteria or not isinstance(row.get('requirement_ids'),list)
                or not set(row.get('requirement_ids') or []) <= ids)]
    if not invalid:
        return None
    return {'error':'Acceptance mapping is outside the active stage; no criterion or link was saved.',
            'not_executed':True,'stage_mapping_scope':scope,'rejected_item_indexes':invalid,
            'next_tool':'project_plan','next_call':scope['read_call'], 'next_action':scope['instruction']}


def stage_mapping_feedback(result, hint):
    scope = stage_mapping_scope(hint)
    if not scope:
        return result
    return {**result,'stage_mapping_scope':scope,
            'next_tool':'project_plan','next_call':scope['read_call'],
            'next_action':('This save made no change. ' if result.get('unchanged') else '')+scope['instruction']}


def scoped_mapping_tools(tools, hint):
    missing = ((hint.get('stage') or {}).get('evidence') or {}).get('missing_deliverables') or []
    if missing:
        import copy
        result = copy.deepcopy([entry for entry in tools if entry['function']['name'] in
                                {'read_file', 'write_file', 'patch_file', 'read_project_requirements', 'project_plan'}])
        for entry in result:
            function = entry['function']
            if function['name'] == 'write_file':
                function['parameters']['properties']['path']['enum'] = [missing[0]]
                function['description'] += ' The host confirmed the next declared file is absent: '+missing[0]+'. Write that complete file next. Do not reread it to confirm absence, map evidence, or request routine permission. This is implementation, not final delivery.'
            if function['name'] == 'project_plan':
                function['parameters']['properties']['action']['enum'] = ['read']
        return result
    scope = stage_mapping_scope(hint)
    if not scope:
        return tools
    import copy
    result = copy.deepcopy([entry for entry in tools if not (coverage_mapping_pending(hint)
                           and entry['function']['name']=='link_acceptance_evidence')])
    for entry in result:
        if entry['function']['name'] != 'acceptance_plan':
            continue
        properties = entry['function']['parameters']['properties']
        if 'has_acceptance_plan' in hint:
            properties['merge'] = {'type': 'boolean', 'const': bool(hint['has_acceptance_plan'])}
            properties['complete'] = {'type': 'boolean', 'const': False}
        for legacy in ('repair','repairs','plan_revision'):
            properties.pop(legacy,None)
        entry['function']['description'] += ' Evidence link repair belongs to link_acceptance_evidence, not this criteria tool.'
        if coverage_mapping_pending(hint):
            entry['function']['description'] += ' All existing criterion links pass. Only the listed missing requirement IDs remain; add semantically relevant rows for those IDs with complete=false and merge=true. Repeating link repair or resaving already covered IDs cannot advance this stage.'
        if 'items' not in properties:
            continue
        fields = properties['items']['items']['properties']
        fields['criterion']['enum'] = list(hint['stage']['criteria'])
        fields['requirement_ids']['items']['enum'] = scope['requirement_ids']
        entry['function']['description'] += ' Mapping writes currently target active stage '+scope['stage_id']+' only. Read project_plan with action=read and stage_id='+scope['stage_id']+' for exact source requirements. Select only relevant original IDs for each criterion; do not resubmit an earlier-stage row. Queries remain available; evidence relevance is still checked.'
    return result


def stage_mapping_options(options, hint, *, has_plan):
    """A stage draft never implicitly claims whole-project coverage."""
    result = dict(options)
    if stage_mapping_scope(hint) and result.get('items') and not result.get('repair'):
        result.update(complete=False, merge=bool(has_plan))
    return result


def stage_link_scope(hint):
    stage = (hint or {}).get('stage') or {}
    scope = None
    if (stage.get('status') in {'active','blocked','pending'} and stage.get('criteria')
            and stage.get('requirement_ids') and (stage.get('evidence') or {}).get('kind') == 'fresh_acceptance'):
        scope = {'stage_id':stage['id'],'criteria':stage['criteria'],'requirement_ids':stage['requirement_ids']}
    return scope


def stage_link_page(acceptance, hint, **options):
    scope = stage_link_scope(hint)
    return acceptance.link_evidence(stage_scope=scope,**options) if scope else acceptance.link_evidence(**options)
