"""Explicit source-span review and auditable user-directed requirement revisions.

The host checks coverage, identity and provenance. The reviewer still checks meaning.
No guessed cancellation, silent omission, or inherited proof grants acceptance.
"""
import copy
import json
import re
import time
from app_maintenance import atomic_json
from project_requirement_catalog import digest, plain, safe_path

KINDS = {'behavior', 'unit', 'browser', 'host', 'context'}
ID_PATTERN = r'R_[a-f0-9]{24}_\d+(?:_[a-f0-9]{12})?'


def source_review_batch(catalog, character_limit=4800):
    """Expose the next source review without requiring a prior passing validation."""
    from project_acceptance import host_only_requirements
    pending=set(catalog.get('pending_obligation_reviews', []))
    blocks=[]
    for row in catalog.get('source_requirements', []):
        if row['id'] not in pending:
            continue
        original=row['text']
        blocks.append({'requirement_id':row['id'], 'text':original[:1600],
                       'text_truncated':len(original)>1600, 'source_length':len(original),
                       'supported_host_evidence':host_only_requirements([row]).get(row['id'], [])})
        if len(blocks)==8:
            break
    value={'catalog_revision':catalog.get('revision'), 'pending_source_reviews':len(pending),
           'source_blocks':blocks, 'next_tool':'review_project_requirements' if pending else 'project_plan',
           'next_action':'Read each original source. If explicitly reviewed as one atomic behavior, use review_atomic_requirement with requirement_id, reviewed_atomic=true, evidence_kind=behavior and expected_revision=catalog_revision. This single-row tool has no segments or host classification. For compound obligations use review_project_requirements with segments and omit whole_source. '
               'Do not resend already saved blocks. Read full original text when truncated. Preserve every behavioral '
               'obligation; context is only for eligible structural headings after explicit review. '
               'While reviews remain, project_plan is unavailable. Finish only the shown source batch, then read the next batch with read_project_requirements. '
               'When pending_source_reviews is zero, use project_plan append_stages to save ONE small stage per call covering at most 16 current IDs; '
               'preserve saved stages and call finalize_draft only when draft.missing_count is zero. Never use save for a partial plan.'}
    while len(json.dumps(value, ensure_ascii=False))>character_limit and len(blocks)>1:
        blocks.pop()
    value['shown_source_blocks']=len(blocks)
    return value


def observe_rejected_review(state, revision, result):
    """Bound rejected review batches even before an execution plan exists."""
    if result.get('saved'):
        state.pop('source_review_rejections', None)
        return False
    if not result.get('error'):
        return False
    tracker = state.get('source_review_rejections') or {}
    if tracker.get('revision') != revision:
        tracker = {'revision': revision, 'failures': 0}
        state['source_review_rejections'] = tracker
    tracker['failures'] += 1
    return tracker['failures'] >= 8


class ReviewError(ValueError):
    def __init__(self,message,key,text,catalog):
        super().__init__(message+' [source requirement '+key+']')
        from project_acceptance import host_only_requirements
        host=host_only_requirements([{'id':key,'text':text}]).get(key,[])
        self.review_repair={'requirement_id':key,'source_text':text[:2000],
            'source_text_truncated':len(text)>2000,'source_length':len(text),
            'expected_revision':catalog['revision'],'batch_saved':False,
            'offset_scope':'Positions in this original requirement text, never positions in a project file.',
            'supported_host_evidence':host,
            'atomic_example_after_explicit_review':{'requirement_id':key,'reviewed_atomic':True,
                'whole_source':True,'evidence_kind':'host' if host else 'behavior'},
            'next_action':'Correct this row, keeping the other correct rows unchanged; the rejected batch saved nothing. '
                'Only after reviewing atomicity use the example, without segments or file offsets. '
                'For compound text split into exact text fragments (occurrence is required when ambiguous), or source-relative spans. '
                'A non-heading obligation must not be context. If supported_host_evidence is empty, review relevant behavioral/unit/browser assertions; do not substitute host-only or unrelated tests. '
                'Preserve passing project code and validation. Do not rerun tests just to repair review metadata.'}


def segment_bounds(text,segment):
    if not isinstance(segment,dict):raise ValueError('Each obligation must be an object')
    if 'text' not in segment:
        if 'occurrence' in segment or 'start' not in segment or 'end' not in segment:
            raise ValueError('Use exact text OR both source-relative start/end positions')
        return segment['start'],segment['end']
    if 'start' in segment or 'end' in segment:raise ValueError('Choose exact text or positions, never both')
    fragment=segment['text']
    if not isinstance(fragment,str) or not fragment.strip():raise ValueError('Exact obligation text must be nonempty')
    matches=[];position=text.find(fragment)
    while position>=0:
        matches.append(position);position=text.find(fragment,position+1)
    occurrence=segment.get('occurrence')
    if not matches:raise ValueError('Exact fragment is absent from this requirement source')
    if occurrence is None:
        if len(matches)!=1:raise ValueError('Exact fragment is ambiguous; specify its one-based occurrence')
        start=matches[0]
    else:
        if type(occurrence) is not int or not 1<=occurrence<=len(matches):raise ValueError('Invalid fragment occurrence')
        start=matches[occurrence-1]
    return start,start+len(fragment)


def classify(text):
    from project_intent import continuation_request
    if continuation_request(text):
        return 'resume'
    value = plain(text).replace('أ','ا').replace('إ','ا').replace('آ','ا')
    if value in {plain(x) for x in ('أكمل','أكمل العمل','أكمل من حيث توقفت','استئناف السلسلة',
            'استكمال السلسلة','تابع','متابعة','continue','resume','resume task','continue where you left off', 'أعد المحاولة السابقة', 'قمت برفع السياق أعد المحاولة السابقة')}:
        return 'resume'
    if re.fullmatch(r'(?:اعد|اع|كرر|اعادة) المحاولة مرة اخرى و?اكمل من حيث توقفت', value):
        return 'resume'
    if re.match(r'^(?:الغ|احذف المتطلب|cancel requirement|remove requirement)\b', value):
        return 'cancel'
    if re.match(r'^(?:عدل|تعديل|استبدل|modify|amend|replace)\b', value):
        return 'amend'
    if value.startswith(('هل يمكنك','هل تستطيع','can you ','could you ','would you ')):
        return 'add'
    # A polite request such as "هل يمكنك إضافة" is an addition, not a question.
    action = re.search(r'\b(?:نفذ|اضف|اضافة|انشئ|ابن|اصلح|غير|implement|add|create|build|fix|change)\b', value)
    if not action and (value.startswith(('ما رايك','ماهي اقتراحات','قدم اقتراحات','اقترح تحسينات',
            'ماذا','لماذا','كيف','هل','what ','why ','how ','is ','does ','suggest improvements'))
            or text.rstrip().endswith(('؟','?'))):
        return 'question'
    return 'add'


def classify_request(request):
    """New messages use a validated model decision; historical records retain fallback behavior."""
    receipt=request.get('interpretation')
    if not receipt:return classify(request['text'])
    from project_message_intake import verified,requirement_text
    intent=verified(request['text'],receipt)['intent']
    if intent in {'continue','repair'}:return 'resume'
    if intent in {'question','unclear'}:return 'question'
    kind=classify(requirement_text(request))
    return kind if kind in {'amend','cancel'} else 'add'


def load(workspace):
    import json
    path = safe_path(workspace, 'requirement-reviews.json')
    value = json.loads(path.read_text('utf-8')) if path.exists() else {'version':1,'reviews':{},'events':[]}
    if value.get('version') != 1 or not isinstance(value.get('reviews'),dict) or not isinstance(value.get('events'),list):
        raise ValueError('Invalid requirement review ledger; preserve it for review')
    return value


def apply(workspace, catalog, requests):
    ledger = load(workspace)
    result = copy.deepcopy(catalog)
    result['source_requirements'] = copy.deepcopy(catalog['requirements'])
    for source in result['source_requirements']:
        source['source_length'] = len(source['text'])
    active, pending, context = [], [], []
    plan_path = safe_path(workspace, 'acceptance-plan.json')
    plan = json.loads(plan_path.read_text(encoding='utf8')) if plan_path.exists() else {}
    mapped_ids = {key for row in plan.get('items', []) for key in row.get('requirement_ids', [])}
    for block in catalog['requirements']:
        # Existing reviewed mappings are retained verbatim. Only orphaned
        # formatting prefixes are excluded from behavioral delivery gates.
        if block.get('structural_role') == 'numbered_list_marker' and block['id'] not in mapped_ids:
            context.append({**block, 'reason': 'Numbered list prefix; the following source block retains the obligation.',
                            'following_requirement_id': block['following_requirement_id']})
            continue
        review = ledger['reviews'].get(block['id'])
        if not review or review['source_sha256'] != digest(block['text']):
            pending.append(block['id'])
            active.append({**block,'logical_id':block['id'],'mandatory':True,'evidence_kind':'behavior'})
            continue
        for segment in review['segments']:
            start,end = segment['start'],segment['end']
            text = block['text'][start:end]
            identity = block['id'] if len(review['segments']) == 1 else block['id']+'_'+digest([start,end,text])[:12]
            if segment['evidence_kind']=='context':
                context.append({**block,'id':identity,'text':text,'source_span':[start,end],
                                'reason':segment['context_reason'],'reviewed_at':review['time']})
                continue
            active.append({**block,'id':identity,'logical_id':identity,'text':text,
                           'source_requirement_id':block['id'],'source_span':[start,end],
                           'mandatory':True,'evidence_kind':segment['evidence_kind'],
                           'reviewed_at':review['time'],'review_revision':review['revision']})
    issues = list(result['review_issues'])
    relations=[]; retired=set()
    by_request={}
    for row in active:
        source=next((s for s in catalog['sources'] if s['id']==row['source_id']),{})
        row['recorded_at']=source.get('recorded_at')
        by_request.setdefault(source.get('request_id'),[]).append(row)
    # Chronology is the durable request order; references can only target predecessors.
    prior={}
    for request in requests:
        kind=classify_request(request)
        new=by_request.get(request['id'],[])
        if kind in {'amend','cancel'}:
            mentioned=list(dict.fromkeys(re.findall(ID_PATTERN,request['text'])))
            if kind=='cancel':
                remainder=re.sub(ID_PATTERN,'',request['text'])
                remainder=plain(remainder).replace('أ','ا').replace('إ','ا').replace('آ','ا')
                remainder=re.sub(r'\b(?:الغ|احذف|المتطلب|المتطلبات|cancel|remove|requirement|requirements|and|و)\b','',remainder).strip()
                if remainder:
                    issues.append(request['id']+': cancellation contains unresolved scope; no obligation removed')
                    continue
            targets=[]
            for key in mentioned:
                matched=[r for r in prior.values() if key in {r['id'],r['logical_id'],r.get('source_requirement_id')}]
                if not matched:
                    issues.append(request['id']+': unknown or retired target '+key)
                targets.extend(matched)
            targets={r['id']:r for r in targets}
            if not targets:
                issues.append(request['id']+': explicit predecessor IDs required for '+kind+'; no obligation removed')
            elif kind=='amend' and not new:
                issues.append(request['id']+': amendment has no replacement obligations')
            else:
                retired.update(targets)
                for key in targets: prior.pop(key,None)
                for row in new:
                    row['predecessors']=list(targets)
                    if len(targets)==1 and len(new)==1:
                        row['logical_id']=next(iter(targets.values()))['logical_id']
                relations.append({'request_id':request['id'],'kind':kind,'time':request.get('time'),
                                  'predecessors':list(targets),'successors':[r['id'] for r in new]})
        prior.update({r['id']:r for r in new})
    result['requirements']=[r for r in active if r['id'] not in retired]
    result['reviewed_structural_context']=context
    if not result['requirements'] and not relations:
        issues.append('No reviewed behavioral or delivery obligations; context alone cannot authorize delivery')
    active_blocks={r.get('source_requirement_id',r['id']) for r in result['requirements']}
    pending=[key for key in pending if key in active_blocks]
    if pending:
        issues.append('Source obligations need explicit span/evidence review: '+', '.join(pending))
    result['retired_requirements']=[r for r in active if r['id'] in retired]
    result['relations']=relations
    result['pending_obligation_reviews']=pending
    result['message_classifications']=[{'request_id':r['id'],'kind':classify_request(r),'time':r.get('time'),
                                       'session_id':r.get('session_id')} for r in requests]
    result['review_issues']=sorted(set(issues))
    result['review_ledger_revision']=digest(ledger)
    result['revision']=digest({k:result[k] for k in ('sources','requirements','relations','review_issues','review_ledger_revision')})
    return result


def source_numbered_heading(catalog, block):
    """Recover lost Markdown structure, never infer headings from wording."""
    source = next((r for r in catalog.get('sources', []) if r['id'] == block.get('source_id')), {})
    text = block['text'].strip()
    lines = [line.strip() for line in source.get('text', '').splitlines() if text in line]
    pattern = r'#{1,6}\s+\d{1,3}\.\s+' + re.escape(text)
    return bool(lines) and all(re.fullmatch(pattern, line) for line in lines)


def review_segments(key,text,item, *, source_heading=False):
    covered=set()
    # An explicit atomic review with a top-level kind already identifies the
    # complete source. Accept the omitted shorthand flag, never explicit false
    # or a mixed span/whole-source request.
    implicit_whole = ('whole_source' not in item and 'segments' not in item
                      and item.get('reviewed_atomic') is True
                      and item.get('evidence_kind') in KINDS)
    if item.get('whole_source') is True or implicit_whole:
        if 'segments' in item:
            raise ValueError('Choose whole_source or segments, never both')
        segments=[{'start':0,'end':len(text),'evidence_kind':item.get('evidence_kind')}]
        if 'context_reason' in item:segments[0]['context_reason']=item['context_reason']
    else:
        if any(field in item for field in ('evidence_kind','context_reason')):
            raise ValueError('Top-level evidence_kind requires whole_source=true')
        segments=item.get('segments')
    if not isinstance(segments,list) or not 1<=len(segments)<=128:
        raise ValueError('Each source block requires 1 to 128 obligations')
    cleaned=[]
    for segment in segments:
        start,end=segment_bounds(text,segment); kind=segment.get('evidence_kind')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(text) or kind not in KINDS:
            raise ValueError('Invalid obligation span or evidence kind')
        positions=set(range(start,end))
        if covered & positions or not text[start:end].strip():
            raise ValueError('Obligations cannot overlap or be empty')
        if kind=='host':
            from project_acceptance import host_only_requirements
            if not host_only_requirements([{'id':key,'text':text[start:end]}]):
                raise ValueError('Behavior cannot be replaced with host-only evidence')
        if kind=='context':
            snippet=text[start:end].strip()
            structural=bool(re.fullmatch(r'(?:#{1,6}\s+[^\n]+|\*\*[^*\n]+\*\*:?)',snippet)) or (source_heading and start == 0 and end == len(text))
            directive=re.search(r'\b(?:must|shall|required|implement|add|support|enable|يجب|يلزم|نفذ|اضف|دعم|انشاء)\b',plain(snippet))
            reason=segment.get('context_reason','')
            if not structural or directive or not isinstance(reason,str) or len(reason.strip())<20:
                raise ValueError('Only explicitly reviewed structural headings may be context; preserve behavioral text as obligations')
        covered.update(positions)
        cleaned.append({'start':start,'end':end,'evidence_kind':kind})
        if kind=='context':cleaned[-1]['context_reason']=reason.strip()
    if any(i not in covered and not c.isspace() for i,c in enumerate(text)):
        missing=[i for i,c in enumerate(text) if i not in covered and not c.isspace()]
        ranges=[]
        for i in missing:
            if ranges and ranges[-1]['end']==i:ranges[-1]['end']=i+1
            else:ranges.append({'start':i,'end':i+1})
        for row in ranges:row['text']=text[row['start']:row['end']]
        raise ValueError('Source text omitted; every non-whitespace character needs an obligation. '
            +json.dumps({'requirement_id':key,'source_length':len(text),'uncovered':ranges,
              'next_action':'Correct review_project_requirements arguments only. For a semantically reviewed atomic block use whole_source=true with evidence_kind and reviewed_atomic=true; compound blocks require complete segments. Do not rewrite project files or rerun passing tests.'},ensure_ascii=False))
    cleaned.sort(key=lambda row:row['start'])
    return cleaned


def review(requirements, *, expected_revision, items):
    catalog=requirements.catalog(persist=False)
    if catalog['revision'] != expected_revision:
        selected = {item.get('requirement_id') for item in items if isinstance(item, dict)
                    and isinstance(item.get('requirement_id'), str)} if isinstance(items, list) else set()
        current = [row for row in catalog['source_requirements'] if row['id'] in selected]
        error = ValueError('Requirements changed; use the current revision and review the affected source rows')
        error.review_repair = {
            'expected_revision': catalog['revision'], 'batch_saved': False,
            'affected_count': len(selected),
            'rows': [{'requirement_id': row['id'], 'text': row['text'][:800],
                      'text_truncated': len(row['text']) > 800} for row in current[:6]],
            'retired_or_unknown_ids': sorted(selected - {row['id'] for row in current})[:6],
            'next_action': 'Review current exact text and resend only affected rows with the current revision. Read full text if truncated.',
        }
        raise error
    if not isinstance(items,list) or not 1 <= len(items) <= 40:
        raise ValueError('Review 1 to 40 source blocks per call')
    blocks={r['id']:r for r in catalog['source_requirements']}
    invalid=[]; seen_ids=set()
    for index, item in enumerate(items):
        key=item.get('requirement_id') if isinstance(item,dict) else None
        reason = ('missing_or_invalid_id' if not isinstance(key,str) else
                  'unknown_source_id' if key not in blocks else
                  'duplicate_source_id' if key in seen_ids else
                  'atomicity_review_not_confirmed' if item.get('reviewed_atomic') is not True else None)
        if reason:
            invalid.append({'index':index,'requirement_id':key[:120] if isinstance(key,str) else None,'reason':reason})
        if isinstance(key,str):seen_ids.add(key)
    if invalid:
        error=ValueError('Use each exact source ID once and confirm atomicity review; see invalid_rows. No rows were saved')
        error.review_repair={'expected_revision':catalog['revision'],'batch_saved':False,
            'invalid_rows':invalid,'next_source_batch':source_review_batch(catalog),
            'next_action':'Correct the listed row indices using exact source IDs from next_source_batch. Do not invent or approximately match IDs. Keep other valid rows unchanged, confirm atomicity, then resend the complete corrected batch. No requirements or files were changed.'}
        raise error
    ledger=load(requirements.workspace)
    now=time.time(); updates={}; seen=set(); failures=[]
    for item in items:
        key=item['requirement_id']
        if key in seen or key not in blocks or item.get('reviewed_atomic') is not True:
            raise ValueError('Use each exact source ID once and confirm atomicity review')
        seen.add(key)
        text=blocks[key]['text']
        try:
            cleaned=review_segments(key,text,item, source_heading=source_numbered_heading(catalog, blocks[key]))
        except (ValueError,KeyError,TypeError) as error:
            failures.append((key,text,error))
            continue
        entry={'source_sha256':digest(text),'segments':cleaned,'time':now,
               'revision':digest([key,cleaned]),'reviewed_atomic':True}
        updates[key]=entry
    if failures:
        key,text,cause=failures[0]
        error=ReviewError(str(cause),key,text,catalog)
        error.review_repair.update(
            invalid_count=len(failures),
            invalid_rows=[{'requirement_id':key,'error':str(cause)[:160]}
                          for key,_,cause in failures],
            valid_block_ids=list(updates),
            next_action='Correct ALL invalid_rows in this batch after reviewing each original source. '
                'Resend the complete batch with the same current revision; no rows were saved. '
                'Keep the valid_block_ids unchanged. Non-heading obligations must remain behavioral/unit/browser '
                'or supported host requirements; do not discard or automatically reclassify them as context. '
                'Use the first-row example only after reviewing its atomicity. Preserve project code and tests.')
        raise error from cause
    # Commit the entire validated batch together, including its prior-review history.
    for key,entry in updates.items():
        if key in ledger['reviews']:
            ledger['events'].append({'requirement_id':key,'previous':ledger['reviews'][key],'time':now})
    ledger['reviews'].update(updates)
    path=safe_path(requirements.workspace,'requirement-reviews.json')
    atomic_json(path,ledger)
    result=requirements.catalog()
    return {'saved':True,'reviewed_blocks':list(updates),
            'resolved_pending_source_reviews':sorted(set(catalog.get('pending_obligation_reviews',[])) - set(result['pending_obligation_reviews'])),
            'catalog_revision':result['revision'],
            'requirements':result['requirements'],
            'pending_obligation_reviews':result['pending_obligation_reviews'],
            'next_source_batch':source_review_batch(result),
            'review_issues':result['review_issues'],
            'scope':'Source coverage checked; atomicity and semantic evidence relevance explicitly reviewed, not inferred by the host.'}
