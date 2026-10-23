"""Request-scoped acceptance mappings backed by fresh named test execution.

Mapping relevance is reviewed by the agent; execution is checked by the host.
Neither text coverage nor a passing test proves semantic completeness.
"""
import hashlib
import json
import re
from project_core import tool
from app_maintenance import atomic_json
from project_validation import VERSION
from project_evidence_repair import evidence_repair
from project_acceptance_bindings import typed_requirements, criterion_requirements, typed_only, receipts as validation_receipts, proposal as repair_proposal, apply as apply_link_repair, preserve_update
from project_acceptance_bindings import preserve_rows

def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def required_host_evidence(text):
    """Recognize explicit delivery constraints only; not a semantic requirement parser."""
    parts = re.split(r'(?<=[.!?;؛؟])\s+|\n+', text)
    if len(parts) > 1:
        return list(dict.fromkeys(test for part in parts for test in required_host_evidence(part)))
    text = text.casefold()
    required = []
    preserve = bool(re.search(r'\b(preserve|unchanged|unmodified)\b|do not (?:edit|modify|change)|لا (?:تعدل|تعدّل|تغير|تغيّر)|دون تعديل|الحفاظ على', text))
    if preserve and re.search(r'(?:preserve|do not (?:edit|modify|change))[^.;\n]{0,180}\btests?\b|\b(?:existing|original) tests?\b[^.;\n]{0,60}(?:unchanged|unmodified)|(?:الحفاظ على|دون تعديل|لا تعدّل|لا تعدل)[^.;\n]{0,100}الاختبارات', text):
        required.append('host:original_tests_unchanged')
    if preserve and re.search(r'(?:preserve|do not (?:edit|modify|change))[^\n]{0,180}readme|readme[^\n]{0,60}(?:unchanged|unmodified)|(?:الحفاظ على|دون تعديل|لا تعدّل|لا تعدل)[^\n]{0,100}(?:readme|ملف التعليمات)', text):
        required.append('host:original_readmes_unchanged')
    if re.search(r'do not (?:export or launch|launch|run)|لا (?:تشغل|تشغّل)', text):
        required.append('host:no_persistent_launch')
    if re.search(r'do not (?:export|launch or export)|لا (?:تصدر|تصدّر)', text):
        required.append('host:no_export')
    if re.search(r'answer in arabic|(?:أجب|الرد|الإجابة) بالعربية', text):
        required.append('host:arabic_response')
    return list(dict.fromkeys(required + typed_requirements(text)))

def host_only_requirements(requirements, initial_tests=None):
    """Conservative whole-clause grammar; mixed behavior is never auto-covered."""
    patterns = (
        r'do not edit (?:existing|original) tests?(?: or readme files?)?',
        r'do not (?:modify|change) (?:existing|original) tests?(?: or readme files?)?',
        r'do not (?:edit|modify|change) readme files?',
        r'preserve (?:existing|original) (?:tests?|readme files?)(?: unchanged)?',
        r'do not (?:export or launch|launch or export) a persistent application',
        r'do not export(?: the (?:project|application))?',
        r'do not launch a persistent application',
        r'answer in arabic',
        r'(?:أجب|الرد|الإجابة) بالعربية',
        r'لا (?:تعدل|تعدّل) الاختبارات الحالية',
    )
    result = {}
    previous = []
    adapter = None
    for row in requirements:
        clause = row['text'].strip().rstrip('.;؛!?؟').strip().casefold()
        if any(re.fullmatch(pattern, clause) for pattern in patterns):
            proof = required_host_evidence(row['text'])
        elif clause == 'preserve their exact bytes' and previous and all(
                p in {'host:original_tests_unchanged', 'host:original_readmes_unchanged'} for p in previous):
            proof = list(previous)
        elif initial_tests is not None and re.fullmatch(r'preserve (?:both )?[\w./-]+ and the adapter unchanged', clause):
            # Resolve a named discovery adapter only from the immediately preceding
            # clause, and only files actually captured in the protected snapshot.
            named = re.fullmatch(r'preserve (?:both )?([\w./-]+) and the adapter unchanged', clause)
            proof = ['host:original_tests_unchanged'] if named and named[1] in initial_tests and adapter in initial_tests else []
        else:
            proof = typed_only(row['text'])
        if proof:
            result[row['id']] = proof
        previous = proof
        match = re.search(r'([\w./-]+) discovery adapter[;.]?$', row['text'], re.I)
        adapter = match[1] if match else None
    return result

ACCEPTANCE_TOOL = tool('acceptance_plan',
    'Repair missing acceptance links without rewriting criteria: read the repair_proposal, then call repair=true with its plan_revision. Supply repairs=[{item_id,old_test,new_test}] only for behavioral IDs after reviewing the actual assertion. Explicit corrections take precedence over automatic suggestions for the same row/link. Executed old links are retained when adding fresh evidence; requesting an already linked ID is a no-op. Typed host obligations are repaired together only for whole host-only requirement clauses. Never remove prior criteria or requirement groups. '
    'Read requirement IDs, exact source text, fresh successful test IDs and current plan (omit items). Large catalogs show first and last IDs together, then the middle on subsequent pages; filter with test_query and continue with test_offset/test_limit. '
    'Save behavior mappings using the exact requirement_ids already supplied in the system requirements. Use complete=false for a reviewed partial draft, then merge=true to add or repair only missing rows. Drafts never authorize delivery; complete fresh coverage is still required. Pure supported delivery constraints are added automatically with actual host checks; mixed or unknown clauses must be mapped explicitly. '
    'Each item states a concrete observable criterion and uses exact successful test IDs listed by the current validate_project result or this tool '
    '(python:module.Class.test, a unique node:test name, or a scoped node:file::suite::name ID). Use one item per distinct behavior. '
    'For delivery constraints use matching host: IDs listed by this tool; for actual browser behavior use browser: IDs '
    'returned by browser_project assertions (reload after source changes). host:arabic_response is evaluated on the final answer. No wildcards or unrelated test mappings. Once relevant exact IDs are available, save the mapping instead of enumerating the catalog. Full original suite execution is already mandatory through validation. '
    'The host automatically attaches matching required_evidence IDs for recognized constraints; their actual observations must pass. '
    'Create meaningful missing tests, validate, then save the mapping. Delivery requires fresh passing evidence for every item. '
    'This is a reviewable test mapping, not proof of semantic completeness.',
    {'repair': {'type': 'boolean'}, 'plan_revision': {'type':'string'},
     'repairs': {'type':'array','maxItems':40,'items':{'type':'object','properties':{'item_id':{'type':'string'},'old_test':{'type':'string'},'new_test':{'type':'string'}},'required':['item_id','old_test','new_test'],'additionalProperties':False}},
     'complete': {'type': 'boolean'}, 'merge': {'type': 'boolean'}, 'test_query': {'type': 'string'}, 'test_offset': {'type': 'integer', 'minimum': 0},
     'test_limit': {'type': 'integer', 'minimum': 1, 'maximum': 256},
     'items': {'type': 'array', 'minItems': 1, 'maxItems': 40, 'items': {'type': 'object',
        'properties': {'criterion': {'type': 'string'},
                       'requirement_ids': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': {'type': 'string'}}} |
                      {'tests': {'type': 'array', 'minItems': 1, 'maxItems': 30, 'items': {'type': 'string'}}},
        'required': ['requirement_ids', 'criterion', 'tests'], 'additionalProperties': False}}})

def new_test_ids(report, initial_tests, workspace):
    """Prioritize actual receipts for newly added test files, without granting passes."""
    new_files = {row['path'] for row in workspace.files() if row['path'] not in initial_tests}
    modules = {name.rsplit('/', 1)[-1][:-3] for name in new_files if name.endswith('.py')}
    result = []
    for check in (report or {}).get('checks', []):
        if not check.get('passed'):
            continue
        identities = check.get('test_identities') or {}
        for test in check.get('successful_tests', []):
            if identities.get(test, {}).get('file') in new_files or (test.startswith('python:') and test[7:].rsplit('.', 2)[0].rsplit('.', 1)[-1] in modules):
                result.append(test)
    return list(dict.fromkeys(result))

class Acceptance:
    def __init__(self, workspace, prompt, brief='', revision='', evidence=None):
        self.workspace = workspace
        from project_acceptance_contract import contract
        contract(workspace)
        self.source = prompt + ('\n' + brief if brief else '')
        sections = [text.strip() for text in re.split(r'(?<=[.!?;؛؟])\s+|\n+', self.source) if text.strip()]
        self.requirements = [{'id': f'R{i+1}', 'text': text,
                              'required_evidence': required_host_evidence(text)} for i, text in enumerate(sections)]
        self.by_id = {row['id']: row['text'] for row in self.requirements}
        self.key = hashlib.sha256((self.source + '\0' + revision).encode()).hexdigest()
        self.evidence = evidence or {}
        self.path = workspace.meta / 'acceptance-plan.json'
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(workspace.meta.resolve()):
            raise ValueError('Invalid acceptance plan path')

    def capture_tests(self):
        path = self.workspace.meta / ('acceptance-tests-' + self.key + '.json')
        if path.is_symlink():
            raise ValueError('Invalid initial test snapshot')
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        hashes = {}
        for row in self.workspace.files():
            name = row['path']
            parts = name.replace('\\', '/').split('/')
            basename = parts[-1].casefold()
            named_test = re.fullmatch(r'test.*\.py|.*\.(?:test|spec)\.[cm]?[jt]sx?', basename)
            if any(part.casefold() in {'test', 'tests', '__tests__'} for part in parts) or named_test or basename.startswith('readme'):
                hashes[name] = file_digest(self.workspace.path(name))
        atomic_json(path, hashes)
        return hashes

    def load(self):
        if not self.path.exists() or self.path.stat().st_size > 2 * 1024 * 1024:
            return None
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return None
        if not isinstance(value, dict):
            return None
        return value if value.get('request_key') == self.key and value.get('version') in {1, 2} else None

    def fresh(self, report):
        from project_performance import contract_current
        from project_source_guard import validation_current as source_current
        contract=getattr(self.workspace,'performance_contract',None)
        quality=getattr(self.workspace,'quality_contract',None)
        return bool(report and report.get('success') and report.get('validator_version') == VERSION
                    and report.get('performance_contract_sha256')==(contract or {}).get('policy_sha256') and contract_current(contract)
                    and report.get('quality_contract_sha256')==(quality or {}).get('policy_sha256') and contract_current(quality)
                    and source_current(self.workspace, report)
                    and report.get('source_unchanged_during_validation') and not report.get('in_progress')
                    and report.get('fingerprint') == self.workspace.fingerprint(force=True))

    def invoke(self, items=None, test_query="", test_offset=0, test_limit=24, merge=False, complete=True, repair=False, plan_revision=None, repairs=None):
        report = self.workspace.last_validation()
        if type(repair) is not bool:raise ValueError("repair must be a boolean")
        if repair:
            if items is not None or merge or test_query or test_offset or not complete:raise ValueError("Link repair cannot be combined with plan/catalog mutations")
            return apply_link_repair(self, report, plan_revision, repairs if repairs is not None else [])
        if repairs is not None or plan_revision is not None:raise ValueError("repairs/plan_revision require repair=true")
        if type(merge) is not bool:
            raise ValueError('merge must be a boolean')
        if type(complete) is not bool:raise ValueError('complete must be a boolean')
        if items is None:
            fresh = self.fresh(report)
            tests = [name for check in (report or {}).get('checks', []) if check.get('passed')
                     and check.get('validation_run_id') and check.get('executed', 0) > 0
                     for name in check.get('successful_tests', [])] if fresh else []
            if not isinstance(test_query, str) or type(test_offset) is not int or test_offset < 0 or type(test_limit) is not int or not 1 <= test_limit <= 256:
                raise ValueError('Invalid test catalog page')
            priority = new_test_ids(report, self.capture_tests(), self.workspace) if fresh else []
            tests = list(dict.fromkeys(priority + tests))
            filtered = [name for name in tests if test_query.casefold() in name.casefold()]
            # Keep the first and last IDs visible together, like validate_project;
            # new regression modules often sort last. Remaining pages retain all IDs.
            if not priority and not test_query and len(filtered) > 64:
                filtered = filtered[:32] + filtered[-32:] + filtered[32:-32]
            page = filtered[test_offset:test_offset + test_limit]
            result = {'requirements': self.requirements, 'plan': self.load(),
                    'test_catalog': {'total': len(tests), 'matched': len(filtered), 'offset': test_offset,
                                     'limit': test_limit, 'query': test_query,
                                     'next_offset': test_offset + len(page) if test_offset + len(page) < len(filtered) else None},
                    'priority_successful_tests': priority[:test_limit],
                    'automatic_requirement_ids': list(host_only_requirements(self.requirements, self.capture_tests())),
                    'mapping_status': self.mapping_status(report),
                    'execution_note': 'Discovery load_tests hooks are not test IDs. Use actual successful behavioral IDs (for example, an original Board mirror test for Board behavior). Use passing typed host receipts for pure suite/adapter execution or preservation obligations. Never replace a behavioral test with a suite receipt. The proposal supplies row-specific executed candidates for assertion review.',
                    'next_step': 'Use the relevant IDs shown here to save acceptance_plan(items=...). Do not query each known ID separately or remap the entire original suite.',
                    'successful_tests': page, 'validation_current': fresh,
                    'available_evidence': {**self.evidence, **validation_receipts(self, report)},
                    'repair_proposal': repair_proposal(self, report),
                    'scope': 'Current request and current user brief; preserve applicable historical requirements.'}
            if fresh and test_query.strip() and not filtered:
                from project_test_suggestions import zero_query_feedback
                result['zero_query_feedback'] = zero_query_feedback(self, report, tests, test_query, priority)
            return result
        if merge:
            plan = self.load()
            if not plan or plan.get('version') != 2 or not isinstance(items, list) or not items:
                raise ValueError('merge requires an existing requirement-ID plan and nonempty updates')
            # Validate incoming rows before using their IDs to remove old rows.
            for row in items:
                if not isinstance(row, dict) or not isinstance(row.get('requirement_ids'), list) or not row['requirement_ids'] or any(key not in self.by_id for key in row['requirement_ids'] if isinstance(key, str)) or any(not isinstance(key, str) for key in row['requirement_ids']):
                    raise ValueError('Updates require valid requirement_ids')
            replaced = {key for row in items for key in row['requirement_ids']}
            retained = [{**row, 'requirement_ids': [key for key in row['requirement_ids'] if key not in replaced]}
                        for row in plan['items'] if any(key not in replaced for key in row['requirement_ids'])]
            items = retained + items
        items = self.check_items(items, require_complete=complete)
        previous = self.load()
        if previous and previous.get("version") == 2:
            covered = {key for row in previous["items"] for key in row.get("requirement_ids", [])}
            if covered == set(self.by_id):preserve_update(previous["items"], items)
        value = {'version': 2 if any('requirement_ids' in row for row in items) else 1,
                 'request_key': self.key, 'items': items}
        atomic_json(self.path, value)
        coverage = self.evaluate(report)
        missing = [{'item': row['id'], 'tests': [t for t in row['missing_tests'] if t != 'host:arabic_response']}
                   for row in coverage['items'] if any(t != 'host:arabic_response' for t in row['missing_tests'])]
        result = {'saved': True, 'items': len(items), 'request_key': self.key,
                'missing_evidence': missing, 'validation_current': self.fresh(report),
                'mapping_status': self.mapping_status(report),
                'repair_proposal': repair_proposal(self, report),
                'next_action': 'Repair only reported or unmapped rows with merge=true.' if missing or coverage.get('error') else 'Deliver the final answer now; final host checks remain mandatory. Do not repeat catalog queries or validation without a source change or actual failure.',
                'note': 'Final delivery checks fresh evidence and the final response; host:arabic_response is evaluated then.'}
        if missing:
            result['evidence_repair'] = evidence_repair(report, missing, self.fresh(report))
            result['next_step'] = self.repair_message(report, coverage)
        return result

    def mapping_status(self, report):
        """Bounded actionable defects; no inferred success or automatic behavior mapping."""
        coverage = self.evaluate(report)
        successful = [name for check in (report or {}).get('checks', [])
                      if check.get('passed') and check.get('validation_run_id') and check.get('executed', 0) > 0
                      for name in check.get('successful_tests', [])] if self.fresh(report) else []
        defects = []
        for row in coverage.get('items', []):
            missing = [test for test in row['missing_tests'] if test != 'host:arabic_response']
            if not missing:
                continue
            # Actual executed IDs only. Suggestions are for human/agent relevance
            # review, never silently substituted into a plan.
            from project_test_suggestions import nearby_tests
            candidates = [] if criterion_requirements(row['criterion']) else nearby_tests(successful, report, row['criterion'])
            if row.get('target_reference_review',{}).get('missing_targets'):
                from project_acceptance_targets import review_candidates
                candidates = review_candidates(self, report, row, successful)
            unranked = not candidates and not criterion_requirements(row['criterion'])
            if unranked:candidates = successful[:6]
            defects.append({'item_id':row['id'], 'requirement_ids': row.get('requirement_ids', []), 'missing_tests': missing,
                            'candidate_successful_tests': candidates,
                            'candidates_are_unranked_examples':unranked,
                            'requires_assertion_review':True,
                            'target_reference_review':row.get('target_reference_review',{})})
        plan=self.load()
        covered={key for row in (plan or {}).get('items',[]) for key in row.get('requirement_ids',[])}
        return {'missing_plan': coverage.get('missing_plan', False), 'error': coverage.get('error'),
                'unmapped_requirements':[row for row in self.requirements if row['id'] not in covered],
                'defects': defects, 'final_response_pending': any('host:arabic_response' in row['missing_tests'] for row in coverage.get('items', [])),
                'ready_for_final_response': bool(coverage.get('mandatory_named_tests_executed')) and not defects and not coverage.get('missing_plan') and not coverage.get('error') and self.fresh(report)}

    def repair_message(self, report, coverage=None):
        coverage = self.evaluate(report) if coverage is None else coverage
        defects = [{'id': row['id'], 'missing_evidence': row['missing_tests']}
                   for row in coverage.get('items', []) if not row['passed']]
        browser_advice = ('A stale receipt is NOT a failed UI assertion. Repeat the previously passing prescribed scenario '
                          'in order, including prerequisite fills and clicks, before interpreting interim snapshots. '
                          'Inspect UI source again only if the completed scenario produces an actual failed assertion. '
                          if any(test.startswith('browser:') for defect in defects for test in defect['missing_evidence']) else '')
        return ('Delivery acceptance blocked. Complete only the unmet checks below; do not repeat a broad review. '
                'If a plan exists, use acceptance_plan(repair=true, plan_revision=...) from the current repair_proposal. This atomically repairs known typed references while preserving ALL criteria and valid links. For unknown behavioral links review the row-specific executed candidates and copy the exact relevant ID in repairs. load_tests is a discovery hook, not an executed test. Missing behavior targets require behavioral tests; do not substitute a full-suite receipt. If mapping_status is ready, deliver instead of replacing already valid links. Do not rewrite the whole plan or revalidate unchanged passing code. '
                'Use required_evidence host IDs for constraints. Finish necessary edits and validation FIRST, '
                'then reload and repeat only required browser assertions if stale; save the mapping and deliver. '
                + browser_advice
                + json.dumps({'validation_current': self.fresh(report),
                              'missing_plan': coverage.get('missing_plan', False),
                              'error': coverage.get('error'), 'defects': defects,
                              'repair_proposal': repair_proposal(self, report),
                              'evidence_repair': evidence_repair(
                                  report, coverage.get('items', []), self.fresh(report))}, ensure_ascii=False))

    def check_items(self, items, require_complete=True):
        if not isinstance(items, list) or not 1 <= len(items) <= 40:
            raise ValueError('Acceptance plan requires 1..40 items')
        if any(isinstance(row, dict) and 'requirement_ids' in row for row in items):
            covered, normalized = set(), []
            for index, row in enumerate(items, 1):
                if not isinstance(row, dict) or not {'requirement_ids', 'criterion', 'tests'} <= set(row) or set(row)-{'requirement_ids', 'criterion', 'tests', 'quote'}:
                    raise ValueError(f'Item {index} needs requirement_ids, criterion, tests')
                ids = row['requirement_ids']
                if not isinstance(ids, list) or not ids or any(not isinstance(key, str) or key not in self.by_id for key in ids):
                    raise ValueError(f'Item {index} contains unknown requirement IDs; read acceptance_plan first')
                self.check_criterion(row)
                ids = list(dict.fromkeys(ids))
                covered.update(ids)
                host_only = host_only_requirements(self.requirements, self.capture_tests())
                required = criterion_requirements(row['criterion']) + [test for key in ids for test in
                            (required_host_evidence(self.by_id[key]) + host_only.get(key, []))]
                from project_acceptance_contract import required as atomic_required
                required += atomic_required(self, '\n'.join(self.by_id[key] for key in ids))
                normalized.append({'requirement_ids': ids, 'quote': '\n'.join(self.by_id[key] for key in ids),
                                   'criterion': row['criterion'], 'tests': list(dict.fromkeys(row['tests'] + required))})
            for key, proofs in host_only_requirements(self.requirements, self.capture_tests()).items():
                if key not in covered:
                    normalized.append({'requirement_ids': [key], 'quote': self.by_id[key],
                                       'criterion': self.by_id[key], 'tests': proofs})
                    covered.add(key)
            if len(normalized) > 40:
                raise ValueError('Acceptance plan exceeds 40 items including delivery constraints')
            missing = [key for key in self.by_id if key not in covered]
            if missing and require_complete:
                raise ValueError('Unmapped requirement IDs: ' + ', '.join(missing) + '; exact requirements: ' + json.dumps(
                    [row for row in self.requirements if row['id'] in missing], ensure_ascii=False))
            return normalized
        # Read compatibility for the initial quote-based plan format.
        covered = set()
        for index, row in enumerate(items, 1):
            if not isinstance(row, dict) or set(row) != {'quote', 'criterion', 'tests'}:
                raise ValueError('Each item needs quote, criterion, tests')
            quote, criterion, tests = row['quote'], row['criterion'], row['tests']
            if not isinstance(quote, str) or not quote.strip() or quote not in self.source:
                raise ValueError(f'Item {index}: quote must be an exact excerpt from acceptance_plan source; use requirement_ids to avoid copying')
            self.check_criterion(row)
            start = self.source.find(quote)
            while start >= 0:
                covered.update(range(start, start + len(quote)))
                start = self.source.find(quote, start + len(quote))
        missing = ''.join(c for i, c in enumerate(self.source) if not c.isspace() and i not in covered)
        if missing:
            raise ValueError('Acceptance source has unmapped text: ' + missing[:700])
        return [{**row, 'tests': list(dict.fromkeys(row['tests'] + required_host_evidence(row['quote'])))} for row in items]

    @staticmethod
    def check_criterion(row):
        criterion, tests = row['criterion'], row['tests']
        if not isinstance(criterion, str) or not 5 <= len(criterion.strip()) <= 2000:
            raise ValueError('State a concrete observable acceptance criterion (5..2000 chars)')
        # Tool input is capped at 30; normalized plans may add host receipts.
        if not isinstance(tests, list) or not 1 <= len(tests) <= 40 or any(
                not isinstance(t, str) or not t.startswith(('python:', 'node:', 'host:', 'browser:')) or '*' in t or len(t) > 600 for t in tests):
            raise ValueError('Use exact named test or host/browser evidence IDs, never generic suite results')

    def evaluate(self, report):
        plan = self.load()
        if not plan:
            return {'passed': False, 'missing_plan': True, 'items': [], 'scope': 'Current request and current brief'}
        try:
            items = self.check_items(plan['items'])
        except (ValueError, KeyError, TypeError) as error:
            return {'passed': False, 'error': str(error), 'items': []}
        successful = set()
        receipts = {}
        fresh = self.fresh(report)
        for check in (report or {}).get('checks', []) if fresh else []:
            if check.get('passed') and check.get('validation_run_id') and check.get('executed', 0) > 0:
                successful.update(check.get('successful_tests', []))
                for test in check.get('successful_tests', []):
                    receipts[test] = {k: check[k] for k in ('name', 'validation_run_id', 'evidence') if k in check}
        tests_executed = bool(successful)
        for test, proof in {**self.evidence, **validation_receipts(self, report)}.items():
            if proof.get('passed') is True:
                successful.add(test)
                receipts[test] = proof
        rows = []
        for i, row in enumerate(items):
            from project_acceptance_targets import relevance
            binding = relevance(self, report or {}, row) if fresh else {'missing_targets':[]}
            from project_acceptance_contract import required as atomic_required
            required = list(dict.fromkeys(required_host_evidence(row['quote']) + criterion_requirements(row['criterion']) + atomic_required(self,row['quote'])))
            missing_links = [test for test in required if test not in row['tests']]
            missing = list(dict.fromkeys([t for t in row['tests'] if t not in successful] + missing_links))
            missing += ['review:target:'+target for target in binding['missing_targets']]
            rows.append({'id': f'A{i+1}', **row, 'passed': not missing, 'target_reference_review':binding,
                         'required_evidence': required, 'missing_links': missing_links, 'missing_tests': missing,
                         'primary_host_evidence': {t:receipts[t] for t in required if t in receipts},
                         'named_tests_require_assertion_review': [t for t in row['tests'] if t.startswith(('python:','node:'))],
                         'evidence': {t: receipts[t] for t in row['tests'] if t in receipts}})
        return {'passed': tests_executed and bool(rows) and all(r['passed'] for r in rows) and self.fresh(report), 'items': rows,
                'mandatory_named_tests_executed': tests_executed,
                'request_key': self.key, 'scope': 'Mapped test execution; semantic relevance requires review'}

def host_evidence(workspace, initial_tests, job, final='', browser=None):
    """Host observations, never caller-supplied/model-declared pass flags."""
    changed = [name for name, digest in initial_tests.items()
               if not workspace.path(name).is_file() or file_digest(workspace.path(name)) != digest]
    fingerprint = workspace.fingerprint(force=True)
    changed_readmes = [name for name in changed if name.rsplit('/', 1)[-1].lower().startswith('readme')]
    changed_tests = [name for name in changed if name not in changed_readmes]
    result = {
        'host:original_tests_unchanged': {'passed': not changed_tests, 'changed': changed_tests},
        'host:original_readmes_unchanged': {'passed': not changed_readmes, 'changed': changed_readmes},
        'host:no_persistent_launch': {'passed': not job.get('run_entry') and workspace.running is None,
                                      'scope': 'No successful persistent launch in this task and no workspace process'},
        'host:no_export': {'passed': not job.get('export'), 'scope': 'No successful export recorded in this task'},
        'host:application_running': {'passed': bool(job.get('run_entry') and workspace.running is not None
                                                    and workspace.running.poll() is None),
                                     'scope': 'Requested workspace process is currently alive; behavior still needs tests'},
        'host:export_current': {'passed': bool(job.get('export') and job['export'].get('source_fingerprint') == fingerprint),
                                 'scope': 'Recorded export matches current source fingerprint'},
        'host:arabic_response': {'passed': len(re.findall('[\u0621-\u064a\u066e-\u06d3\u06fa-\u06fc]', final)) >= 5,
                                 'scope': 'Response contains Arabic text; does not prove linguistic quality'},
    }
    for name, proof in (browser or {}).items():
        result[name] = {**proof, 'passed': bool(proof.get('passed') and proof.get('fingerprint') == fingerprint)}
    return result
