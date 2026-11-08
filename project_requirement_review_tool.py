from project_core import tool


def scoped_review_tools(tools, batch):
    """Expose valid review shapes and exact IDs from the current source page."""
    import copy
    if not batch or not batch.get('pending_source_reviews'):
        return tools
    ids = [row['requirement_id'] for row in batch.get('source_blocks', [])]
    result = copy.deepcopy(tools)
    for entry in result:
        function = entry['function']
        if function['name'] not in {'review_project_requirements', 'review_atomic_requirement'}:
            continue
        schema = function['parameters']
        if function['name'] == 'review_atomic_requirement':
            fields = schema['properties']
            fields['reviewed_atomic']['const'] = True
            if ids:
                fields['requirement_id']['enum'] = ids
            continue
        rows = schema['properties']['items']
        rows['maxItems'] = min(4, len(ids)) if ids else 1
        item = rows['items']; fields = item['properties']
        fields['reviewed_atomic']['const'] = True
        if ids:
            fields['requirement_id']['enum'] = ids
        common = {key: copy.deepcopy(fields[key]) for key in ('requirement_id', 'reviewed_atomic')}
        whole = dict(common, whole_source={'type': 'boolean', 'const': True},
                     evidence_kind=copy.deepcopy(fields['evidence_kind']),
                     context_reason=copy.deepcopy(fields['context_reason']))
        whole['evidence_kind']['enum'] = ['behavior', 'unit', 'browser', 'host']
        context = copy.deepcopy(whole)
        context['evidence_kind'] = {'type': 'string', 'const': 'context'}
        split = dict(common, segments=copy.deepcopy(fields['segments']))
        item['anyOf'] = [
            {'type': 'object', 'properties': whole,
             'required': ['requirement_id', 'reviewed_atomic', 'whole_source', 'evidence_kind'],
             'additionalProperties': False},
            {'type': 'object', 'properties': context,
             'required': ['requirement_id', 'reviewed_atomic', 'whole_source', 'evidence_kind', 'context_reason'],
             'additionalProperties': False},
            {'type': 'object', 'properties': split,
             'required': ['requirement_id', 'reviewed_atomic', 'segments'],
             'additionalProperties': False}]
        function['description'] += (' Review up to FOUR displayed source blocks per call, each independently. Confirm reviewed_atomic=true '
                                    'after reviewing its obligations. Use whole_source=true plus evidence_kind '
                                    'OR segments with a kind per segment, never both forms. '
                                    'The next response supplies the next pending sources.')
    return result


def review_progress_result(result, catalog):
    """Return the exact next source page without repeating the whole catalog."""
    from project_requirement_lifecycle import source_review_batch
    pending = catalog.get('pending_obligation_reviews', [])
    value = dict(result, catalog_revision=catalog['revision'], next_source_batch=source_review_batch(catalog))
    if pending:
        for key in ('requirements', 'source_requirements', 'pending_obligation_reviews', 'review_issues'):
            value.pop(key, None)
        value['pending_source_reviews'] = len(pending)
    else:
        value.update(requirements=catalog['requirements'], pending_obligation_reviews=[],
                     review_issues=catalog.get('review_issues', []))
    return value

REVIEW_REQUIREMENTS_TOOL = tool('review_project_requirements',
    'Review original source blocks into atomic obligations using exact character spans. '
    'Read source_requirements and the revision first. For a reviewed atomic block use whole_source=true and evidence_kind; the host computes exact bounds. Do not include segments with whole_source. Preserve every source character; '
    'Split compound obligations using segments with exact original text (and one-based occurrence when ambiguous), OR source-relative start/end. Never use project-file offsets. Select relevant unit/browser/host evidence. '
    'Only structural Markdown headings with no behavioral directive may be context, '
    'with a context_reason of at least 20 characters; their source is kept in the audit. '
    'This records your explicit semantic review, not automatic host understanding. '
    'It cannot cancel requirements; cancellations/amendments require original user requests with exact predecessor IDs.',
    {'expected_revision':{'type':'string'},'items':{'type':'array','minItems':1,'maxItems':40,'items':{
        'type':'object','properties':{'requirement_id':{'type':'string'},'reviewed_atomic':{'type':'boolean'},
        'whole_source':{'type':'boolean'},'evidence_kind':{'type':'string','enum':['behavior','unit','browser','host','context']},
        'context_reason':{'type':'string','minLength':20},
        'segments':{'type':'array','minItems':1,'maxItems':128,'items':{'type':'object','properties':{
            'text':{'type':'string','minLength':1},'occurrence':{'type':'integer','minimum':1},
            'start':{'type':'integer','minimum':0},'end':{'type':'integer','minimum':1},
            'evidence_kind':{'type':'string','enum':['behavior','unit','browser','host','context']},
            'context_reason':{'type':'string','minLength':20}},
            'required':['evidence_kind'],'additionalProperties':False}}},
        'required':['requirement_id','reviewed_atomic'],'additionalProperties':False}}},
    ['expected_revision','items'])


ATOMIC_REVIEW_TOOL = tool('review_atomic_requirement',
    'Review ONE original source block only AFTER reading it and explicitly deciding it is atomic. '
    'For behavior choose behavior, unit or browser; this tool cannot downgrade it to host/context. '
    'The host computes exact whole-source bounds; no segments can be supplied. '
    'For compound text, host constraints or structural headings use review_project_requirements instead.',
    {'expected_revision': {'type': 'string'}, 'requirement_id': {'type': 'string'},
     'reviewed_atomic': {'type': 'boolean'},
     'evidence_kind': {'type': 'string', 'enum': ['behavior', 'unit', 'browser']}},
    ['expected_revision', 'requirement_id', 'reviewed_atomic', 'evidence_kind'])


def atomic_review_arguments(arguments):
    if set(arguments) != {'expected_revision', 'requirement_id', 'reviewed_atomic', 'evidence_kind'} or arguments.get('reviewed_atomic') is not True or arguments.get('evidence_kind') not in {'behavior', 'unit', 'browser'}:
        raise ValueError('Read the original source and explicitly confirm an atomic behavioral obligation; use the full review tool for other cases')
    return {'expected_revision': arguments['expected_revision'], 'items': [{
        'requirement_id': arguments['requirement_id'], 'reviewed_atomic': True,
        'whole_source': True, 'evidence_kind': arguments['evidence_kind']}]}
