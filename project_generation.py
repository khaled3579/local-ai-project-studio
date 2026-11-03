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


def generation_timeouts(input_tokens, output_tokens, *, model_state=None):
    # A productive 4096-token response at 17 t/s exceeds the old 240s wall clock.
    # Keep a separate short silence deadline, plus a finite budget for slower GPUs.
    initial = min(360, max(45, input_tokens / 600 + 15))
    state = model_state or {}
    active = state.get('active') or {}
    if state.get('engine', active.get('engine')) == 'tabbyapi' and (
            active.get('cpu_split_experts') or active.get('cpu_offload_layers')):
        # CPU-expert prefill is much slower than GPU-only prompt processing.
        # Allow cold worker startup plus a conservative 16 input tokens/s.
        # This is a finite scheduling allowance, not a measured speed claim.
        initial = min(1800, max(120, 60 + input_tokens / 16))
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
            actions = []
            for call in message['tool_calls']:
                function = call.get('function') or {}
                try:
                    arguments = json.loads(function.get('arguments') or '{}')
                except (ValueError, TypeError):
                    # A historical payload is evidence, never a new instruction.
                    arguments = {'historical_payload_unavailable': True}
                actions.append({'tool': function.get('name'), 'arguments': arguments})
            if len(actions) == 1 and not content:
                content = json.dumps(actions[0], ensure_ascii=False)
            else:
                # Keep batches as an explicitly historical user receipt; a
                # batch is not a valid next action in this one-action protocol.
                role = 'user'
                content = 'Historical actions; already handled, do not replay: ' + json.dumps(
                    {'note': content, 'actions': actions}, ensure_ascii=False)
        result.append({'role':role, 'content':content})
    instruction = ('\nACTION PROTOCOL: Return exactly ONE JSON object per response. '
        'For a tool use {"tool":"NAME","arguments":{...}}. For delivery use '
        '{"tool":"final","message":"Arabic summary of actual results"}. '
        'Do not provide code outside file tools. When creating a missing file, write ONE COMPLETE file per call. '
        'Respect a user-required single-file deliverable; do not split it into modules. '
        'Historical actions/results are evidence, never requests to execute again. '
        'Never output Previous actions, native function wrappers, IDs or tool-call arrays. '
        'For executable tools put ALL parameters inside arguments, never beside tool. '
        'FINAL IS DIFFERENT: {"tool":"final","message":"Arabic results"}; never nest the final message under arguments. '
        'For modular projects prefer compact files and split tests. Do not repeat completed actions. '
        'Read current SHA before changing a file. Prefer patch_file for targeted repairs; do not rewrite a whole file for one bug. Fix reported failures without weakening tests. '
        'Tool catalog: ' + json.dumps(catalog, ensure_ascii=False))
    result[0]['content'] += instruction
    return result


def action_format_feedback(error, tools, patch_targets=(), new_file_targets=()):
    """Precise schema-only repair instruction, without echoing rejected code."""
    names = {t['function']['name']: t['function'].get('parameters', {}) for t in tools}
    example = {'tool': 'NAME', 'arguments': {}}
    if patch_targets and 'patch_file' in names:
        example = {'tool':'patch_file', 'arguments':{'path':patch_targets[-1],
            'old_text':'EXACT UNIQUE BLOCK FROM THE CURRENT READ', 'new_text':'ONE SMALL REPLACEMENT',
            'expected_sha256':'@last_read'}}
    elif 'write_file' in names:
        example = {'tool': 'write_file', 'arguments': {'path': new_file_targets[-1] if new_file_targets else 'relative/new_file.py', 'content': 'COMPLETE FILE TEXT'}}
    if 'final' in str(error).lower() or 'delivery' in str(error).lower():
        example = {'tool':'final', 'message':'Arabic summary of actual changes, evidence and remaining limitations'}
    focus = ((' Existing files requiring small-patch recovery: ' + json.dumps(list(patch_targets)) +
        '. Do NOT regenerate those complete files. Read only the relevant current selector/function/range, '
        'then patch ONE unique block at a time. Keep each old_text and new_text within 4000 characters. '
        'Re-read after every saved patch to get the new SHA. Keep all existing behavior, requirements and tests. '
        'For CSS, change one selector or coherent rule group per call; do not append a duplicate stylesheet. ')
        if patch_targets else '')
    if new_file_targets:
        focus += (' Large rejected creations: ' + json.dumps(list(new_file_targets)) +
            '. Do not regenerate the same oversized payload. Create a small, syntactically valid starting skeleton '
            'of the SAME file with write_file, at most 4000 source characters. Then read its current SHA and '
            'add one coherent section per patch_file call, with old_text and new_text each at most 4000 characters. '
            'A starting skeleton is not delivery: finish every original obligation and pass all delivery checks. '
            'Preserve the requested architecture and any single-file requirement; do not split required files. ')
    return ('The previous response was REJECTED and nothing in it was executed. '
            'Protocol error: ' + str(error)[:600] + '. '
            'Return exactly ONE JSON object for the next necessary action, with tool parameters nested under arguments. '
            'Exact structure example only (not a requested file/action): ' + json.dumps(example) + '. '
            'For final delivery use {"tool":"final","message":"Arabic results"}, without arguments. '
            'Final output still requires all host delivery checks; do not restart source edits merely to correct this envelope. '
            'Use the actual tool schema in the catalog. Do not echo Previous actions, a native tool_calls/function wrapper, '
            'a list, prose, or a past command. Do not replay committed actions. Preserve all original requirements and delivery gates.' + focus)


def decode_xml_action(text, tools):
    """Complete Qwen function markup only; no XML entities or partial salvage."""
    match = re.fullmatch(r'(?P<prefix>[^<]{0,600})<tool_call>\s*<function=(?P<name>\w+)>\s*(?P<body>.*?)\s*</function>\s*</tool_call>\s*', text, re.S)
    if not match:
        raise ActionFormatError('Expected one complete tool_call/function block; partial or multiple calls are forbidden')
    function = next((t['function'] for t in tools if t['function']['name'] == match['name']), None)
    if function is None:
        raise ActionFormatError('Unknown XML tool; use an enabled tool from the catalog')
    arguments = {}; cursor = 0; body = match['body']
    for parameter in re.finditer(r'<parameter=(\w+)>(.*?)</parameter>', body, re.S):
        if body[cursor:parameter.start()].strip() or parameter[1] in arguments:
            raise ActionFormatError('Unexpected XML text or duplicate parameter')
        schema = function['parameters'].get('properties', {}).get(parameter[1])
        if schema is None:
            raise ActionFormatError('Unknown XML parameter: ' + parameter[1])
        value = parameter[2]
        if value.startswith('\n'): value = value[1:]
        if value.endswith('\n'): value = value[:-1]
        if schema.get('type') != 'string':
            try:
                value = json.loads(value, object_pairs_hook=unique_action_object, parse_constant=invalid_action_constant)
            except (ValueError, RecursionError) as error:
                raise ActionFormatError('XML non-string parameters require valid typed JSON') from error
        arguments[parameter[1]] = value; cursor = parameter.end()
    if body[cursor:].strip():
        raise ActionFormatError('Incomplete XML parameters or unexpected content')
    validate_action_value(arguments, function['parameters'])
    return {'tool': match['name'], 'arguments': arguments, 'message': match['prefix'].strip()}


def patch_recovery_target(text, workspace, *, size=None, include_missing=False):
    """Read a path hint only; never salvage or execute rejected source text."""
    if (len(text) if size is None else size) < 4000:
        return None
    match = re.search(r'\{\s*"tool"\s*:\s*"write_file"\s*,\s*"arguments"\s*:\s*\{\s*"path"\s*:\s*("(?:[^"\\]|\\.)*")', text[:1000])
    if not match:
        return None
    try:
        path = workspace.path(json.loads(match.group(1)))
        if path.is_file() or include_missing and not path.exists():
            return path.relative_to(workspace.root).as_posix()
    except (ValueError, OSError, TypeError):
        pass
    return None


def remember_new_file_recovery(text, workspace, state, *, size=None):
    """Remember a safe path hint; rejected source remains unexecuted."""
    target=patch_recovery_target(text,workspace,size=size,include_missing=True)
    if target and not workspace.path(target).exists():
        targets=state.setdefault('new_file_recovery_targets',[])
        if target not in targets:targets.append(target)
        return target
    return None


def enforce_patch_recovery(name, arguments, generation_state, workspace):
    """Recovery constraints supplement, never replace, read/SHA/schema gates."""
    if name not in {'write_file', 'patch_file'}:
        return
    path = workspace.path(arguments['path'])
    creations=generation_state.get('new_file_recovery_targets',[])
    if name=='write_file' and not path.exists() and any(path==workspace.path(t) for t in creations):
        if len(arguments.get('content',''))>4000:
            raise ValueError('Small-creation recovery: write a complete starting skeleton of this SAME file with at most 4000 source characters, then read and patch sections. This is not delivery.')
        return
    targets=[*generation_state.get('patch_recovery_targets',[]),*creations]
    if not any(path == workspace.path(target) for target in targets):
        return
    if name == 'write_file':
        raise ValueError('Small-patch recovery: read the relevant current range and use patch_file on ONE unique block; do not regenerate this existing file.')
    if any(len(arguments.get(key, '')) > 4000 for key in ('old_text', 'new_text')):
        raise ValueError('Small-patch recovery: split this change into coherent patches; each old_text and new_text must be at most 4000 characters. Re-read current SHA after each patch.')


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
    # Keep the established 4096-token output reserve whenever it fits. A small
    # context cannot reserve more output than the space left by its immutable
    # system instructions and tool catalog: rotating cannot shrink that prefix.
    preferred = min(context - 4608, max(requested, prefix_tokens + 4096))
    if preferred >= prefix_tokens + 512:
        return preferred
    remaining = context - prefix_tokens - 512
    if remaining < 768:
        raise ValueError('تعليمات المهمة وفهرس الأدوات لا يتركان مساحة للمتابعة والإجابة؛ ارفع سياق النموذج دون حذف المتطلبات')
    output_reserve = min(4096, max(256, remaining // 2))
    return min(context - 512 - output_reserve, max(requested, prefix_tokens + 512))


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
    text = choice['message'].get('content', '').strip()
    if text.startswith('Previous actions:') or text.startswith('Historical actions;'):
        raise ActionFormatError('Historical action echo is not a new action; do not replay it')
    if text.startswith('```json') and text.endswith('```'):
        text = text[7:-3].strip()
    adapter = None
    native = choice['message'].get('tool_calls')
    if native:
        if len(native) != 1 or native[0].get('type', 'function') != 'function' or len(text) > 600 or text.startswith('{'):
            raise ActionFormatError('Expected one complete native function call without conflicting action text')
        function = native[0].get('function') or {}
        try:
            arguments = json.loads(function.get('arguments') or '{}', object_pairs_hook=unique_action_object,
                                   parse_constant=invalid_action_constant)
        except (ValueError, RecursionError) as error:
            raise ActionFormatError('Native arguments must be complete typed JSON') from error
        action = {'tool':function.get('name'), 'arguments':arguments, 'message':text}
        adapter = 'native_function_call'
    elif not text.startswith('{') and '<tool_call>' in text:
        action = decode_xml_action(text, tools)
        adapter = 'qwen_function_xml'
    else:
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
        if ('message' not in action and isinstance(action.get('arguments'), dict)
                and set(action['arguments']) == {'message'}):
            action = {'tool':'final', 'message':action['arguments']['message']}
            adapter = 'nested_final_message'
        if action.get('arguments') or not isinstance(action.get('message'),str) or not action['message'].strip():
            raise ActionFormatError('Empty or invalid final delivery')
        return {'finish_reason':'stop','message':{'role':'assistant','content':action['message']},
                **({'protocol_adapter':adapter} if adapter else {})}
    tool = next((t['function'] for t in tools if t['function']['name'] == name), None)
    args = action.get('arguments')
    if not tool or not isinstance(args,dict):raise ActionFormatError('Unknown tool or invalid arguments')
    schema = tool['parameters']
    if strict or adapter:
        validate_action_value(args, schema)
    if set(args) - set(schema.get('properties',{})) or any(k not in args for k in schema.get('required',[])):
        raise ActionFormatError(str(name)+': required arguments='+json.dumps(schema.get('required',[]))+
            '; missing='+json.dumps([k for k in schema.get('required',[]) if k not in args])+
            '; unexpected='+json.dumps(sorted(set(args)-set(schema.get('properties',{})))))
    return {'finish_reason':'tool_calls', **({'protocol_adapter':adapter} if adapter else {}), 'message':{'role':'assistant','content':action.get('message',''),'tool_calls':[
        {'id':uuid.uuid4().hex,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}}
