from project_core import tool

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
