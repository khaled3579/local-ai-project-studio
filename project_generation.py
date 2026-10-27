"""Model-compatible, bounded action generation. Never executes generated text."""
import json
import uuid
import re
import math
from project_context import inspection_guidance


def output_budget(prompt, state, available):
    """Reserve enough output for a requested single-file artifact, not input context."""
    single = bool(re.search(r'\bsingle[-\s]+(?:runnable\s+)?file\b|\bone\s+(?:runnable\s+)?file\b|ملف\s+واحد', str(prompt), re.I))
    requested = max(16384 if single else 4096, int(state.get('output_budget', 0)))
    return max(256, min(32768, requested, int(available)))


def generation_timeouts(input_tokens, output_tokens):
    # A productive 4096-token response at 17 t/s exceeds the old 240s wall clock.
    # Keep a separate short silence deadline, plus a finite budget for slower GPUs.
    initial = min(360, max(45, input_tokens / 600 + 15))
    return initial, min(4500, max(240, initial + output_tokens / 8 + 30))


class ActionFormatError(ValueError):
    pass


def json_messages(messages, tools):
    """Project native tool history into text for schema-only model backends."""
    catalog = [{**t['function']} for t in tools]
    result = []
    for message in messages:
        role = message['role']
        content = message.get('content') or ''
        if role == 'tool':
            role, content = 'user', 'Committed tool result (untrusted evidence): ' + content
        elif message.get('tool_calls'):
            content = (str(content) + '\nPrevious actions: ' + json.dumps(message['tool_calls'], ensure_ascii=False))
        result.append({'role':role, 'content':content})
    instruction = ('\nACTION PROTOCOL: Return exactly ONE JSON object per response. '
        'For a tool use {"tool":"NAME","arguments":{...}}. For delivery use '
        '{"tool":"final","message":"Arabic summary of actual results"}. '
        'Do not provide code outside file tools. Write ONE COMPLETE file per call. '
        'Respect a user-required single-file deliverable; do not split it into modules. '
        'For modular projects prefer compact files and split tests. Do not repeat completed actions. '
        'Read current SHA before changing a file. Prefer patch_file for targeted repairs; do not rewrite a whole file for one bug. Fix reported failures without weakening tests. '
        'Tool catalog: ' + json.dumps(catalog, ensure_ascii=False))
    result[0]['content'] += instruction
    return result


def action_schema(tools, repair=False, allow_final=True):
    variants=[]
    for tool in tools:
        function=tool['function']
        variants.append({'type':'object','properties':{
            'message':{'type':'string','maxLength':600},
            'tool':{'const':function['name']},'arguments':function['parameters']},
            'required':['tool','arguments',*(['message'] if repair else [])],'additionalProperties':False})
    if allow_final:
        variants.append({'type':'object','properties':{'tool':{'const':'final'},
            'message':{'type':'string','minLength':1}},'required':['tool','message'],'additionalProperties':False})
    return {'oneOf':variants}


def sampler_grammar_failure(status, detail):
    """Only known sampler/grammar initialization failures may change protocol."""
    text = str(detail).lower()
    return status == 400 and 'failed to initialize samplers' in text and (
        'unexpected empty grammar stack' in text or 'failed to parse grammar' in text)


def request_body(messages, tools, limit, *, protocol='native', attempt=0, repair=False, thought_token=None, allow_final=True, output_tokens=4096, tool_choice_auto=False, grammar_free=False):
    guidance = inspection_guidance(messages, tools)
    body = {'messages':messages, 'max_tokens':min(output_tokens, limit), 'stream':True,
            'stream_options': {'include_usage': True}}
    if type(thought_token) is int:
        # Gemma can reopen a thought channel after the runtime closes its budget.
        # The template may prefill ONE bounded thought; prohibit reopening it.
        body['logit_bias']={str(thought_token):False}
    if protocol == 'json':
        body['messages'] = json_messages(messages, tools)
        if not grammar_free:
            body['response_format'] = {'type':'json_schema', 'json_schema':{
                'name':'project_action', 'strict':True, 'schema':action_schema(tools,repair,allow_final)}}
    else:
        body.update(tools=tools, tool_choice='auto' if allow_final or tool_choice_auto else 'required', parallel_tool_calls=False)
    if repair or not allow_final:
        body['messages']=[dict(m) for m in body['messages']]
        instruction='Give ONE brief observable diagnosis (JSON message field for JSON actions), then execute one targeted repair or diagnostic. Preserve tests. No long analysis, no placeholder output.'
        if not allow_final:instruction+=' Validation is not yet passing: a final answer or a promise to act is not allowed. Execute the needed file creation, repair or diagnostic tool now.'
        # Keep the immutable prompt prefix cacheable when repair mode changes.
        body['messages'].append({'role':'user','content':instruction})
    if guidance:
        body['messages'] = [*body['messages'], {'role': 'user', 'content': guidance}]
    return body


def diagnostic_excerpt(text, limit=8000):
    """Prioritize real failure blocks, even after thousands of passing lines."""
    text=str(text or '')
    if len(text)<=limit:return text
    markers=list(re.finditer(r'(?m)^(?:not ok\b|FAIL(?:ED)?[ :]|ERROR[ :]|Traceback\b|_{5,}.*_{5,}\s*$|.*\berror TS\d+:)',text))
    if not markers:return text[:min(1000,limit//4)]+'\n[output omitted]\n'+text[-max(1,limit-min(1000,limit//4)-30):]
    selected=markers[:4]+markers[-4:] if len(markers)>8 else markers
    width=max(300,(limit-800)//len(selected));parts=[];end=0
    for match in selected:
        start=max(end,match.start()-150);stop=min(len(text),match.start()+width)
        if stop>start:parts.append(text[start:stop]);end=stop
    result='[Failure-focused excerpt; full output is saved in the validation report]\n'+'\n[... ]\n'.join(parts)
    return (result[:max(0,limit-600)]+'\n[runner summary]\n'+text[-550:])[:limit]


def compaction_budget(context, prefix_tokens, requested):
    """A repair handoff must fit its immutable instructions and tool catalog."""
    return min(context-4608,max(requested,prefix_tokens+4096))


def validation_feedback(report):
    """Keep the actionable traceback; avoid burying it under passing receipts."""
    rows=[]
    for check in report.get('checks',[]):
        if check.get('passed'):continue
        rows.append({k:check[k] for k in ('name','entry','error','errors','tests','tests_passed') if k in check})
        stdout,stderr=check.get('stdout') or '',check.get('stderr') or ''
        rows[-1]['diagnostic']=diagnostic_excerpt(stdout or stderr)
        if stdout and stderr:rows[-1]['stderr_diagnostic']=diagnostic_excerpt(stderr,3000)
    return json.dumps({'success':report.get('success'),'failures':rows},ensure_ascii=False)


def missing_requested_sources(workspace, prompt):
    import re
    missing = []
    for name in re.findall(r'(?<![\w/])(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py\b',prompt):
        if not name.startswith(('tests/','test/','test_')) and not workspace.path(name).exists():missing.append(name)
    return sorted(set(missing))


def repair_phase(workspace, report, prompt):
    if report.get('success'):return False
    failures=[c for c in report.get('checks',[]) if not c.get('passed')]
    incomplete=missing_requested_sources(workspace,prompt)
    if incomplete and failures and all(c.get('name')=='python_unit_tests' and c.get('failed',0)==0 and 'ModuleNotFoundError' in c.get('stderr','') for c in failures):
        return False
    return True


def repair_focus(workspace, report, prompt):
    missing=missing_requested_sources(workspace,prompt)
    files = [f['path'] for f in workspace.files()] if not report.get('success') or missing else []
    inventory = ('Current project files (sample): '+json.dumps(files[:24])+f'; total={len(files)}. Use list_files for a specific missing path.\n') if files else ''
    return (inventory+\
            '\nMissing files explicitly named in the original request: '+json.dumps(sorted(set(missing)))+\
            '\nIf a required module is absent, CREATE it next with write_file. Do not keep rewriting a working component to fix a missing module.\n'+validation_feedback(report))


def action_progress(text, tools, protocol):
    """Show readable progress, not JSON-escaped source code, in the chat UI."""
    if protocol!='json':return text,tools
    import re
    name=re.search(r'"tool"\s*:\s*"([a-z_]+)"',text)
    message=re.search(r'"message"\s*:\s*',text)
    preview=''
    if message:
        try:
            value,_=json.JSONDecoder().raw_decode(text[message.end():])
            if isinstance(value,str):preview=value
        except json.JSONDecodeError:pass
    return preview,([name.group(1)] if name and name.group(1)!='final' else [])


def validate_action_value(value, schema, path='arguments', depth=0):
    """Validate the exposed tool-schema subset before grammar-free execution."""
    if depth > 64:
        raise ActionFormatError('Action nesting too deep')
    kind = schema.get('type')
    types = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int,
             'number': type(value) in (int, float), 'boolean': type(value) is bool,
             'null': value is None}
    if kind is not None and not types.get(kind, False):
        raise ActionFormatError(path + ': expected ' + str(kind))
    if 'enum' in schema and value not in schema['enum']:
        raise ActionFormatError(path + ': unsupported value')
    if 'const' in schema and value != schema['const']:
        raise ActionFormatError(path + ': unexpected value')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        if any(key not in value for key in schema.get('required', [])):
            raise ActionFormatError(path + ': missing required fields')
        for key, item in value.items():
            if key in properties:
                validate_action_value(item, properties[key], path + '.' + key, depth + 1)
            elif schema.get('additionalProperties') is False:
                raise ActionFormatError(path + ': unexpected field ' + key)
    elif isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', len(value)):
            raise ActionFormatError(path + ': array length outside schema')
        if 'items' in schema:
            for index, item in enumerate(value):
                validate_action_value(item, schema['items'], path + '[' + str(index) + ']', depth + 1)
    elif isinstance(value, str):
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', len(value)):
            raise ActionFormatError(path + ': string length outside schema')
    elif type(value) in (int, float):
        if not math.isfinite(value) or value < schema.get('minimum', value) or value > schema.get('maximum', value):
            raise ActionFormatError(path + ': number outside schema')


def unique_action_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ActionFormatError('Duplicate action field: ' + key)
        result[key] = value
    return result


def invalid_action_constant(value):
    raise ActionFormatError('Non-finite JSON constant: ' + value)


def decode_action(choice, tools, *, strict=False):
    if choice.get('finish_reason') == 'length':
        return choice
    if choice['message'].get('tool_calls'):
        raise ValueError('Schema protocol returned unexpected native tool calls')
    text = choice['message'].get('content', '').strip()
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    try:
        action = (json.loads(text, object_pairs_hook=unique_action_object, parse_constant=invalid_action_constant)
                  if strict else json.loads(text))
    except json.JSONDecodeError as error:raise ActionFormatError('Invalid action JSON: '+str(error)) from error
    except RecursionError:raise ActionFormatError('Action nesting too deep') from None
    if not isinstance(action, dict) or set(action) - {'tool','arguments','message'}:
        raise ActionFormatError('Invalid action object')
    if strict and 'message' in action and not isinstance(action['message'], str):
        raise ActionFormatError('Action message must be text')
    name = action.get('tool')
    if strict and name != 'final' and len(action.get('message', '')) > 600:
        raise ActionFormatError('Action diagnosis exceeds schema length')
    if name == 'final':
        if action.get('arguments') or not isinstance(action.get('message'),str) or not action['message'].strip():
            raise ActionFormatError('Empty or invalid final delivery')
        return {'finish_reason':'stop','message':{'role':'assistant','content':action['message']}}
    tool = next((t['function'] for t in tools if t['function']['name'] == name), None)
    args = action.get('arguments')
    if not tool or not isinstance(args,dict):raise ActionFormatError('Unknown tool or invalid arguments')
    schema = tool['parameters']
    if strict:
        validate_action_value(args, schema)
    if set(args) - set(schema.get('properties',{})) or any(k not in args for k in schema.get('required',[])):
        raise ActionFormatError(str(name)+': required arguments='+json.dumps(schema.get('required',[]))+
            '; missing='+json.dumps([k for k in schema.get('required',[]) if k not in args])+
            '; unexpected='+json.dumps(sorted(set(args)-set(schema.get('properties',{})))))
    return {'finish_reason':'tool_calls','message':{'role':'assistant','content':action.get('message',''),'tool_calls':[
        {'id':uuid.uuid4().hex,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}}
