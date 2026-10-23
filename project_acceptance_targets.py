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


def relevance(owner,report,row):
    from project_acceptance_bindings import host_only_row
    named=[name for name in row['tests'] if name.startswith(('node:','python:'))]
    if host_only_row(owner,row) or not named and any(name.startswith('browser:') for name in row['tests']):
        return {'targets':[],'missing_targets':[],'irrelevant_tests':[]}
    defined=symbols(owner.workspace,report.get('fingerprint'))
    identifiers=re.findall(r'\b(?:[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*_[a-z0-9_]+)(?:\.[A-Za-z_][A-Za-z0-9_]*)?',row['criterion'])
    targets=list(dict.fromkeys(value for value in identifiers if value.split('.')[0] in defined))
    refs={name:reference_tokens(test_reference(owner,report,name)) for name in named}
    covered={target for target in targets if any(target_referenced(target,tokens) for tokens in refs.values())}
    irrelevant=[name for name,tokens in refs.items() if targets and not any(target_referenced(target,tokens) for target in targets)]
    return {'targets':targets,'missing_targets':[target for target in targets if target not in covered],
            'irrelevant_tests':irrelevant,
            'source_reference_diagnostics':{name:proof for name,proof in getattr(owner.workspace,'_acceptance_reference_diagnostics',{}).items() if name in named},
            'scope':'Whole identifier references to explicit existing code targets are checked without substring or separator merging. This is not proof of assertion completeness or receiver binding. Full suites, independent behavior and mutation audits remain required.'}


def review_candidates(owner, report, row, known, limit=6):
    """Offer existing executed IDs for uncovered symbols; never auto-map behavior."""
    from project_test_suggestions import words
    binding = relevance(owner, report, row)
    targets = binding.get('missing_targets') or binding.get('targets') or []
    terms = words(row['criterion'])
    ranked = []
    for name in known:
        if not name.startswith(('python:', 'node:')):
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
