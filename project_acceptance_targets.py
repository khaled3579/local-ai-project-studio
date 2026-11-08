"""Conservative target-reference checks; assertion meaning still requires review."""
import ast
import re


def symbols(workspace,fp):
    cached=getattr(workspace,'_acceptance_target_symbols',None)
    if cached and cached[0]==fp:return cached[1]
    result=set()
    for row in workspace.files():
        name=row['path'];parts=name.replace('\\','/').split('/')
        if any(part.casefold() in {'tests','test','__tests__'} for part in parts) or re.search(r'(?:\.test|\.spec)\.',name):continue
        path=workspace.path(name)
        if path.suffix not in {'.py','.js','.mjs','.cjs','.ts'} or path.stat().st_size>2*1024*1024:continue
        source=path.read_text(encoding='utf-8-sig',errors='replace')
        if path.suffix=='.py':
            try:tree=ast.parse(source)
            except SyntaxError:continue
            result.update(node.name for node in ast.walk(tree) if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)))
        else:result.update(re.findall(r'\b(?:class|function)\s+([A-Za-z_$][\w$]*)',source))
    workspace._acceptance_target_symbols=(fp,result)
    return result


def normalize(text):return re.sub(r'[^a-z0-9]','',text.casefold())


def reference_tokens(text):
    """Keep identifier boundaries; fuzzy normalization is for suggestions only."""
    return {part.casefold() for part in re.findall(r'[^\W\d][\w$]*|\$[\w$]*', text, flags=re.UNICODE)}


def target_referenced(target, tokens):
    return all(part.casefold() in tokens for part in target.split('.'))


def test_reference(owner,report,name):
    fp=(report.get('fingerprint'), tuple(str(c.get('validation_run_id')) for c in report.get('checks',[])))
    cached=getattr(owner.workspace,'_acceptance_test_references',None)
    if not cached or cached[0]!=fp:
        cached=(fp,{})
        owner.workspace._acceptance_test_references=cached
        owner.workspace._acceptance_reference_diagnostics={}
    if name not in cached[1]:cached[1][name]=_test_reference(owner,report,name)
    return cached[1][name]


def _test_reference(owner,report,name):
    for check in report.get('checks',[]):
        identity=(check.get('test_identities') or {}).get(name)
        if identity:
            # File, suite and leaf are actual runner identities, never guessed aliases.
            if name.startswith('node:') and name in check.get('successful_tests',[]) and check.get('passed') and check.get('validation_run_id'):
                from project_acceptance_references import node_reference
                source=node_reference(owner.workspace,report,identity)
                owner.workspace._acceptance_reference_diagnostics[name]={key:value for key,value in source.items() if key!='identifiers'} | {'identifier_count':len(source['identifiers'])}
                return name+' '+json_identity(identity)+' '+' '.join(source['identifiers'])
            return name+' '+json_identity(identity)
    if name.startswith('python:'):
        fields=name[7:].split('.')
        if len(fields)>=3:
            module,klass,method='.'.join(fields[:-2]),fields[-2],fields[-1]
            for relative in (module.replace('.','/')+'.py','tests/'+module.replace('.','/')+'.py'):
                path=owner.workspace.path(relative)
                if not path.is_file():continue
                try:tree=ast.parse(path.read_text(encoding='utf-8-sig'))
                except (OSError,SyntaxError):continue
                for node in ast.walk(tree):
                    if isinstance(node,ast.ClassDef) and node.name==klass:
                        for function in node.body:
                            if isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)) and function.name==method:
                                # Actual method call references allow opaque test names.
                                return name+' '+ast.unparse(function)
    return name


def json_identity(identity):
    return ' '.join(str(identity.get(key,'')) for key in ('file','suite','name'))


def required_observables(criterion):
    """Reject obvious category mismatches, not a semantic approval oracle."""
    text = criterion.casefold()
    # Protecting access to a dashboard is backend authentication, not a claim
    # about its rendered UI. Remove only that narrow security noun phrase from
    # classification; keep every other UI/mutation/format signal and the source.
    text = re.sub(r'حماية\s+لوحة\s+(?:ال)?تحكم(?=[^\n]*(?:مصادقة|session auth|jwt|rbac))',
                  'حماية منطقة الإدارة', text)
    required = []
    if re.search(r'\bcsv\b', text): required.append('csv-content')
    if re.search(r'\bwebp\b', text): required.append('webp-format')
    if re.search(r'drag.{0,12}drop|سحب.{0,20}(?:إفلات|افلات)|multi.select|متعدد.{0,12}اختيار|responsive|متجاوب|quick.edit|(?:تعديل|تحرير) سريع|thumbnail|مصغرة|(?:print.{0,20}invoice|invoice.{0,20}print)|طباعة.{0,12}فاتور|(?:admin|sales|orders).{0,12}dashboard|dashboard.{0,40}(?:sales|orders|stock|kpi)|admin.{0,30}(?:add|edit)|(?:admin|user).{0,70}(?:minute|second)|لوحة.{0,12}تحكم', text):
        required.append('ui-observation')
    if re.search(r'drag.{0,12}drop|سحب.{0,20}(?:إفلات|افلات)|multi.select|quick.edit|(?:تعديل|تحرير) سريع|admin.{0,30}(?:add|edit)|(?:print.{0,20}invoice|invoice.{0,20}print)|طباعة.{0,12}فاتور|browser.{0,12}click',text):
        if 'ui-observation' not in required: required.append('ui-observation')
        required.append('ui-interaction')
    if re.search(r'admin.{0,30}(?:add|edit)|quick.edit|(?:تعديل|تحرير) سريع',text):
        required.append('persisted-mutation')
    if 'webp-format' in required and 'ui-observation' in required:
        required.append('decoded-webp-output')
    return required


def python_assertions(workspace, name, include_actions=False):
    fields = name[7:].split('.')
    if len(fields) < 3: return ([],[]) if include_actions else []
    module, klass, method = '.'.join(fields[:-2]), fields[-2], fields[-1]
    for relative in (module.replace('.', '/')+'.py', 'tests/'+module.replace('.', '/')+'.py'):
        path = workspace.path(relative)
        if not path.is_file() or path.stat().st_size > 2*1024*1024: continue
        try: tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        except (OSError, SyntaxError, UnicodeError): continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef) or cls.name != klass: continue
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != method: continue
                values = {}
                def own_nodes(node):
                    yield node
                    for child in ast.iter_child_nodes(node):
                        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                            yield from own_nodes(child)
                nodes = list(own_nodes(fn))
                aliases = {}
                for node in tree.body:
                    if isinstance(node, ast.Import):
                        aliases.update({a.asname or a.name: a.name for a in node.names})
                    elif isinstance(node, ast.ImportFrom):
                        aliases.update({a.asname or a.name: (node.module or '')+'.'+a.name for a in node.names})
                # Expand local/module constants and returned content aliases.
                # Bounded static inspection does not execute arbitrary test code.
                for node in [*tree.body, *nodes]:
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name): values[target.id] = node.value
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                        values[node.target.id] = node.value
                def data(node, seen=frozenset(), depth=0):
                    if depth > 8: return ''
                    if isinstance(node, ast.Name) and node.id in values and node.id not in seen:
                        return data(values[node.id], seen | {node.id}, depth+1)
                    if isinstance(node, ast.Constant): return repr(node.value)
                    if isinstance(node, ast.Name): return aliases.get(node.id, node.id)
                    if isinstance(node, ast.Attribute): return data(node.value, seen, depth+1)+'.'+node.attr
                    return ' '.join(data(child, seen, depth+1) for child in ast.iter_child_nodes(node))
                result = []
                for node in nodes:
                    if isinstance(node, ast.Assert): result.append(data(node.test))
                    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr.startswith('assert'):
                        count = 1 if node.func.attr in {'assertTrue', 'assertFalse', 'assertIsNone', 'assertIsNotNone'} else 2
                        result.append(' '.join(data(arg) for arg in node.args[:count]))
                actions=[n.func.attr for n in nodes if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in {'click','fill','select_option','press','dispatch_event','send_keys','check','uncheck','post','put','patch','delete','setItem'}]
                return (result[:40],actions[:40]) if include_actions else result[:40]
    return ([],[]) if include_actions else []


def observed(owner, report, name):
    if name.startswith('python:'):
        assertions,actions = python_assertions(owner.workspace, name, include_actions=True)
    else:
        test_reference(owner, report, name)
        assertions = getattr(owner.workspace, '_acceptance_reference_diagnostics', {}).get(name, {}).get('observable_assertions', [])
        actions = getattr(owner.workspace, '_acceptance_reference_diagnostics', {}).get(name, {}).get('observable_actions', [])
    result = set()
    for value in assertions:
        text = value.casefold()
        # Require the asserted value to be format/content related. An endpoint
        # name, image filename, fixture name or status code alone is insufficient.
        if re.search(r'(?:content.type|mimetype|headers).{0,100}(?:text/csv|application/csv)|(?:csv\s*\.\s*(?:dictreader|reader))|csv.{0,60}parse|(?:text/csv|application/csv).{0,100}(?:content.type|mimetype|headers)', text):
            result.add('csv-content')
        if re.search(r'(?:\.format|content.type|mimetype|headers).{0,100}(?:[\'\"]webp[\'\"]|image/webp)|(?:[\'\"]webp[\'\"]|image/webp).{0,100}(?:\.format|content.type|mimetype|headers)|riff.{0,70}webp', text):
            result.add('webp-format')
        if re.search(r'\.type.{0,100}image/webp|image/webp.{0,100}\.type',text):
            # Blob.type is the browser/Node MIME output; filename extensions or
            # visible labels are not MIME/byte assertions.
            result.add('webp-format')
        if re.search(r'(?:document|getelementbyid|queryselector|page\.|locator)', text) and re.search(r'(?:text_content|textcontent|inner_text|innertext|is_visible|isvisible|bounding_box|boundingclientrect|getcomputedstyle|scrollwidth|clientwidth|innerwidth|input_?value|selectedoptions|checked|\.value\b)', text):
            result.add('ui-observation')
    if 'ui-observation' in result and any(re.search(r'(?:^|\.)(?:click|fill|selectOption|select_option|press|dispatchEvent|dispatch_event|onclick|send_keys|check|uncheck)$',action) for action in actions):
        result.add('ui-interaction')
    if any(re.search(r'(?:^|\.)(?:post|put|patch|delete|setItem)$',action) for action in actions) and any(re.search(r'\.(?:json|get_json|body|data|query|getItem)\b',value) for value in assertions):
        result.add('persisted-mutation')
    return result


def observable_review(owner, report, row, named):
    required = required_observables(row['criterion']+'\n'+row.get('quote', ''))
    if not required: return {'required': [], 'missing': []}
    successful = {name for c in report.get('checks', []) if c.get('passed') and c.get('validation_run_id') and c.get('executed', 0)>0 for name in c.get('successful_tests', [])}
    available = set()
    for name in named:
        if name in successful: available.update(observed(owner, report, name))
    # Only host-issued, current browser assertions can supply UI observations.
    for name in row['tests']:
        proof = owner.evidence.get(name, {})
        if name.startswith('browser:') and proof.get('passed') is True and proof.get('fingerprint') == report.get('fingerprint'):
            assertion = proof.get('assertion') or {}; interaction = assertion.get('interaction') or {}
            image = assertion.get('image_format') or {}
            if (assertion.get('passed') is True and assertion.get('source') == 'image_format' and assertion.get('actual') == 'image/webp'
                    and image.get('mime') == 'image/webp' and image.get('signature') == 'RIFF/WEBP' and image.get('decoded') is True and image.get('bytes',0) >= 20):
                available.add('webp-format')
                available.add('decoded-webp-output')
            if assertion.get('source') == 'image_format' and not re.search(
                    r'image|webp|media|photo|thumbnail|zoom|صور|صورة|وسائط|ضغط',row['criterion'],re.I):
                # An actual decoded image proves its format, not a dashboard
                # number, order status, theme color or responsive layout.
                continue
            available.add('ui-observation')
            if (assertion.get('passed') is True and assertion.get('source') in {'text','value','checked','count','dialog_count','image_format'}
                    and interaction.get('action') in {'click','fill','select','press','upload'} and interaction.get('state_changed') is True):
                available.add('ui-interaction')
                if interaction.get('mutation_observed') is True:
                    available.add('persisted-mutation')
    missing = [kind for kind in required if kind not in available]
    return {'required': required, 'missing': missing,
            'guidance': 'Inspect actual asserted output: CSV content; WebP format/bytes. For browser conversion, put the actual converter output in an IMG data URL via FileReader and use browser_project assert target=image_format expected=image/webp. Text labels, constants, mocked canvas encoders and prebuilt output fixtures do not prove actual encoded output. UI WebP requires decoded-webp-output from a current host browser image_format assertion; mocked unit encoding remains useful unit coverage only. Interactive UI criteria need an executed DOM event test, or a browser action plus passing state assertion and host-observed change. Visibility/enabled alone is structure. Admin add/edit and quick-edit also need persisted-mutation: a named write-request/storage test asserting returned or stored data, or a browser action with successful owned-origin write response and changed state. A GET or merely filling a field is not saving a record. Preserve criteria and valid links.',
            'scope': 'Conservative absence checks only. Presence does not establish complete behavior, timing, browser integration or human review; assertion relevance still requires review.'}


def relevance(owner,report,row):
    report = report or {}
    from project_acceptance_bindings import host_only_row
    named=[name for name in row['tests'] if name.startswith(('node:','python:'))]
    if host_only_row(owner,row):
        return {'targets':[],'missing_targets':[],'irrelevant_tests':[]}
    observable = observable_review(owner, report, row, named)
    if not named and any(name.startswith('browser:') for name in row['tests']):
        return {'targets': [], 'missing_targets': ['observable:'+kind for kind in observable['missing']],
                'irrelevant_tests': [], 'observable_review': observable}
    defined=symbols(owner.workspace,report.get('fingerprint'))
    identifiers=re.findall(r'\b(?:[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*_[a-z0-9_]+)(?:\.[A-Za-z_][A-Za-z0-9_]*)?',row['criterion'])
    targets=list(dict.fromkeys(value for value in identifiers if value.split('.')[0] in defined))
    refs={name:reference_tokens(test_reference(owner,report,name)) for name in named}
    covered={target for target in targets if any(target_referenced(target,tokens) for tokens in refs.values())}
    irrelevant=[name for name,tokens in refs.items() if targets and not any(target_referenced(target,tokens) for target in targets)]
    return {'targets':targets,'missing_targets':[target for target in targets if target not in covered]+['observable:'+kind for kind in observable['missing']],
            'observable_review':observable,
            'irrelevant_tests':irrelevant,
            'source_reference_diagnostics':{name:{key:value for key,value in proof.items() if key not in {'observable_assertions','observable_actions'}} | {'observable_assertion_count':len(proof.get('observable_assertions', [])), 'observable_action_count':len(proof.get('observable_actions', []))} for name,proof in getattr(owner.workspace,'_acceptance_reference_diagnostics',{}).items() if name in named},
            'scope':'Whole identifier references to explicit existing code targets are checked without substring or separator merging. This is not proof of assertion completeness or receiver binding. Full suites, independent behavior and mutation audits remain required.'}


def review_candidates(owner, report, row, known, limit=6):
    """Offer existing executed IDs for uncovered symbols; never auto-map behavior."""
    from project_test_suggestions import words
    binding = relevance(owner, report, row)
    targets = [t for t in (binding.get('missing_targets') or binding.get('targets') or []) if not t.startswith('observable:')]
    terms = words(row['criterion'])
    ranked = []
    for name in known:
        if not name.startswith(('python:', 'node:')):
            continue
        # Suggestions may contribute one missing observable to a composite
        # criterion. Actual link validation still requires the complete union
        # of current executed evidence; a suggestion never establishes a pass.
        missing = set(binding.get('observable_review', {}).get('missing', []))
        if missing and not missing.intersection(observed(owner, report, name)):
            continue
        text = normalize(name)
        matches = sum(all(normalize(part) in text for part in target.split('.')) for target in targets)
        if targets and not matches:
            continue
        candidate_terms = words(name)
        shared = sum(any(a == b or len(a) >= 5 and len(b) >= 5 and (a.startswith(b) or b.startswith(a))
                         for b in candidate_terms) for a in terms)
        if not matches and not shared:
            continue
        exact_classes = sum(bool(re.search(r'(?:^|[.:])'+re.escape(target.split('.')[0])+r'(?:TestCase|Tests?|[.:]|$)', name)) for target in targets)
        ranked.append((matches, exact_classes, shared, name))
    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    return [name for _, _, _, name in ranked[:limit]]


def observable_sources(requirements, focus):
    """Explain which original mapped clause requests the specific proof kind."""
    if not isinstance(requirements,list):return []
    missing=set(focus.get('missing') or [])
    preferred=next((set(group)&missing for group in [
        ('decoded-webp-output','webp-format'),('csv-content',),('persisted-mutation',),('ui-interaction',),('ui-observation',)] if set(group)&missing),set())
    ids=set(focus.get('requirement_ids') or [])
    result=[]
    for row in requirements:
        text=row.get('text') or row.get('quote') or ''
        if row.get('id') not in ids or not (set(required_observables(text))&preferred):continue
        detail={'requirement_id':row['id'],'required_observables':sorted(set(required_observables(text))&preferred)}
        if len(text)<=1000:detail['text']=text
        else:detail['requires_full_source']=True
        result.append(detail)
        if len(result)==3:break
    return result
