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


def targeted_patch_clause(text):
    clause=text.strip().rstrip('.;؛!?؟').strip().casefold()
    return bool(re.fullmatch(r'(?:use|apply) (?:a )?targeted patch(?: to (?:change|fix|modify) the existing implementation)?|استخدم رقعة محددة لتعديل التنفيذ الحالي',clause))


def targeted_patch_evidence(workspace,initial_tests,job):
    proof={'passed':False,'files':[],'scope':'An existing non-test source has a host-recorded patch whose result matches current bytes. This is process evidence, not behavioral correctness.'}
    from project_source_guard import baseline_current
    pin=getattr(workspace,'source_guard_baseline',None)
    if not pin or pin.get('id')!=job.get('base_version') or not baseline_current(workspace):return proof
    baseline={r['path']:r for r in workspace.history.get(pin['id'])['files']}
    for name,receipt in job.get('efficiency',{}).get('source_patch_receipts',{}).items():
        if name not in baseline or name in initial_tests or '/tests/' in ('/'+name) or workspace.path(name).suffix.lower() not in {'.py','.pyw','.js','.mjs','.cjs','.ts','.tsx','.jsx','.html','.css'}:continue
        path=workspace.path(name)
        if path.is_file() and receipt.get('before_fingerprint')!=receipt.get('after_fingerprint') and receipt.get('sha256')!=baseline[name]['sha256'] and file_digest(path)==receipt.get('sha256'):
            proof['files'].append(name)
    proof['passed']=bool(proof['files']);return proof

def required_host_evidence(text):
    """Recognize explicit delivery constraints only; not a semantic requirement parser."""
    parts = re.split(r'(?<=[.!?;؛؟])\s+|\n+', text)
    if len(parts) > 1:
        return list(dict.fromkeys(test for part in parts for test in required_host_evidence(part)))
    text = text.casefold()
    required = []
    if targeted_patch_clause(text):required.append('host:targeted_source_patch')
    from project_comment_evidence import clause as comment_clause
    if comment_clause(text):required.append('host:source_comments_preserved')
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
        if any(re.fullmatch(pattern, clause) for pattern in patterns) or targeted_patch_clause(row['text']) or __import__('project_comment_evidence').clause(row['text']):
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
    'Read or edit acceptance criteria and requirement mappings. For existing rows with missing or invalid evidence, use link_acceptance_evidence. '
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

LINK_EVIDENCE_TOOL = tool('link_acceptance_evidence',
    'Link evidence to existing acceptance rows. Omit repairs to read a page of unresolved rows. '
    'Copy the returned item_id and missing old_test, review the actual assertion, then supply its exact executed new_test. '
    'Use repairs=[] to apply available typed host replacements. The host selects the current revision. '
    'Use acceptance_plan only for criteria or requirement mapping changes.',
    {'offset': {'type': 'integer', 'minimum': 0},
     'repairs': ACCEPTANCE_TOOL['function']['parameters']['properties']['repairs']})


def track_acceptance_attempt(state, before, after, query=None):
    """Bound attempts across tool names and across changing rejected arguments."""
    if state.get('snapshot') != before or before != after:
        state.clear()
        state.update(snapshot=after, mutations=0, queries={})
    if before != after:
        return False
    if query is None:
        state['mutations'] += 1
        return state['mutations'] >= 8
    queries = state['queries']
    queries[query] = queries.get(query, 0) + 1
    return queries[query] >= 4 or len(queries) >= 64


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
    def __init__(self, workspace, prompt, brief='', revision='', evidence=None, catalog=None):
        self.workspace = workspace
        from project_acceptance_contract import contract
        contract(workspace)
        self.source = prompt + ('\n' + brief if brief else '')
        sections = [text.strip() for text in re.split(r'(?<=[.!?;؛؟])\s+|\n+', self.source) if text.strip()]
        self.requirements = [{'id': f'R{i+1}', 'text': text,
                              'required_evidence': required_host_evidence(text)} for i, text in enumerate(sections)]
        self.by_id = {row['id']: row['text'] for row in self.requirements}
        self.key = hashlib.sha256((self.source + '\0' + revision).encode()).hexdigest()
        self.catalog = catalog
        if catalog is not None:
            self.source = '\n'.join(row['text'] for row in catalog['requirements'])
            self.requirements = [{**row, 'required_evidence':required_host_evidence(row['text'])}
                                 for row in catalog['requirements']]
            self.by_id = {row['id']:row['text'] for row in self.requirements}
            self.key = hashlib.sha256(('project-catalog-v1\0' + catalog['revision']).encode()).hexdigest()
        self.evidence = evidence or {}
        self.path = workspace.meta / 'acceptance-plan.json'
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(workspace.meta.resolve()):
            raise ValueError('Invalid acceptance plan path')

    def capture_tests(self):
        pinned = getattr(self.workspace, 'current_acceptance_initial_tests', None)
        if pinned is not None:
            return dict(pinned)
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
        if value.get('request_key') == self.key and value.get('version') in {1, 2}:
            return value
        if self.catalog and value.get('catalog_revision') and value.get('version') == 2:
            # Carry only exact stable source-ID mappings. Evidence freshness is
            # still evaluated against the current source and host receipts.
            items = [row for row in value.get('items', []) if row.get('requirement_ids')
                and all(key in self.by_id for key in row['requirement_ids'])
                and row.get('quote') == '\n'.join(self.by_id[key] for key in row['requirement_ids'])]
            if items:
                return {**value, 'request_key':self.key, 'items':items,
                        'inherited_from':value['request_key']}
        return None

    def archive_previous_plan(self):
        if not self.path.exists(): return
        raw = self.path.read_bytes()
        directory = self.workspace.meta / 'acceptance-plan-history'
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.workspace.meta.resolve()):
            raise ValueError('Invalid acceptance history path')
        directory.mkdir(exist_ok=True)
        target = directory / (hashlib.sha256(raw).hexdigest() + '.json')
        if target.is_symlink(): raise ValueError('Invalid acceptance history entry')
        if not target.exists():
            from project_core import atomic_replace
            temporary = target.with_suffix('.tmp')
            if temporary.is_symlink(): raise ValueError('Invalid acceptance history temporary path')
            temporary.write_bytes(raw); atomic_replace(temporary, target)

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

    def link_evidence(self, offset=0, repairs=None, *, stage_scope=None):
        if type(offset) is not int or offset < 0:
            raise ValueError('offset must be a non-negative integer')
        report = self.workspace.last_validation()
        plan = self.load()
        current = repair_proposal(self, report)
        rows = []
        ids = {}
        from project_acceptance_targets import observable_sources
        for row in current['repairs']:
            item_id = row['item_id']
            criterion = plan['items'][int(item_id[1:]) - 1]
            # WorkPlan.evidence gates the stage on every exact matching criterion,
            # including historical rows whose requirement IDs belong elsewhere.
            # Hiding those rows makes an unsatisfied gate impossible to repair.
            # This exposes its existing links only; no requirement is reassigned.
            if stage_scope and criterion['criterion'] not in stage_scope['criteria']:
                continue
            identity = [criterion['criterion'], criterion.get('requirement_ids', []), criterion.get('quote', '')]
            key = item_id + ':' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:16]
            ids[key] = item_id
            rows.append({
                'item_id': key, 'requirement_ids': row['requirement_ids'],
                'criterion': row['criterion'][:500], 'missing_tests': row['missing_tests'],
                'candidate_successful_tests': row['candidate_successful_tests'][:8],
                'automatic_replacements': row['automatic_replacements'],
                'missing_targets': row['target_reference_review'].get('missing_targets', []),
                'observable_review': row['target_reference_review'].get('observable_review', {}),
                'required_source_requirements': observable_sources(self.requirements, {'requirement_ids':row['requirement_ids'],'missing':row['target_reference_review'].get('observable_review',{}).get('missing',[])}),
                'existing_links': criterion['tests'],
                'repairable_existing_links': [link for link in criterion['tests'] if link in row['missing_tests']],
                'existing_links_are_only_valid_old_test_values': True,
                'add_evidence_guidance': 'First repair the exact repairable_existing_links when present; choosing a valid old link only appends evidence and leaves these defects unresolved. If no stored invalid link remains and an existing executed link is valid but insufficient, copy it as old_test and add the new relevant executed ID as new_test. The existing valid link is retained. Missing review:target markers are diagnostics, not old_test IDs. If no relevant candidate exists, implement a relevant test or browser scenario and obtain fresh evidence first.',
                'next_action': ('Review a relevant executed assertion before linking.'
                                if row['candidate_successful_tests'] or row['automatic_replacements']
                                else 'Capture the missing named test or typed host/browser evidence.'),
            })
        # Ordering only: historical proofs never become current or accepted here.
        # Prefer missing implementation/assertions before replaying stale scenarios
        # which another source edit would immediately invalidate again.
        deferred = 0
        if self.fresh(report):
            for entry in rows:
                criterion = plan['items'][int(ids[entry['item_id']][1:]) - 1]
                stale, supported = set(), set()
                for name in criterion['tests']:
                    proof = self.evidence.get(name, {})
                    assertion = proof.get('assertion') or {}
                    if (not name.startswith('browser:') or not (proof.get('passed') is True or proof.get('observed_passed') is True)
                            or assertion.get('passed') is not True or proof.get('fingerprint') == report.get('fingerprint')):
                        continue
                    stale.add(name); supported.add('observable:ui-observation')
                    interaction = assertion.get('interaction') or {}
                    if (assertion.get('source') in {'text','value','checked','count','dialog_count','image_format'}
                            and interaction.get('action') in {'click','fill','select','press','upload'} and interaction.get('state_changed') is True):
                        supported.add('observable:ui-interaction')
                        if interaction.get('mutation_observed') is True: supported.add('observable:persisted-mutation')
                    image = assertion.get('image_format') or {}
                    if (assertion.get('source') == 'image_format' and assertion.get('actual') == 'image/webp'
                            and image.get('mime') == 'image/webp' and image.get('signature') == 'RIFF/WEBP'
                            and image.get('decoded') is True and image.get('bytes',0) >= 20):
                        supported.add('observable:webp-format')
                        supported.add('observable:decoded-webp-output')
                targets = set(entry['missing_targets'])
                missing = set(entry['missing_tests'])
                refresh_only = bool(stale and targets <= supported and missing
                                    and all(name in stale or (name.startswith('review:target:') and name.removeprefix('review:target:') in supported) for name in missing))
                entry['evidence_state'] = 'refresh_after_source_stable' if refresh_only else 'needs_evidence_or_implementation'
                if refresh_only:
                    deferred += 1
                    entry['next_action'] = ('Historical linked browser evidence is stale, not accepted. Finish remaining implementation/test changes first, '
                        'then validate stable source and refresh this exact relevant scenario once. Never reuse stale evidence for delivery.')
            rows.sort(key=lambda entry: entry.get('evidence_state') == 'refresh_after_source_stable')
        page = {'rows': rows[offset:offset + 3], 'total': len(rows),
                'next_offset': offset + 3 if offset + 3 < len(rows) else None,
                'validation_current': current['validation_current']}
        # Host-issued browser IDs must be rediscoverable after conversation
        # compaction. These are available proofs, not automatic row mappings.
        browser_proofs = []
        if self.fresh(report):
            for name, proof in self.evidence.items():
                if (not name.startswith('browser:') or proof.get('passed') is not True
                        or proof.get('fingerprint') != report.get('fingerprint')):
                    continue
                assertion = proof.get('assertion') or {}
                if assertion.get('passed') is not True:
                    continue
                interaction = assertion.get('interaction') or {}
                browser_proofs.append({'evidence_id': name,
                    'selector': str(assertion.get('selector', ''))[:240],
                    'source': assertion.get('source'),
                    'browser_context': proof.get('browser_context'),
                    'image_format': assertion.get('image_format'),
                    'expected': str(assertion.get('expected', ''))[:300],
                    'actual': str(assertion.get('actual', ''))[:300],
                    'action': interaction.get('action'),
                    'state_changed': interaction.get('state_changed') is True,
                    'mutation_observed': interaction.get('mutation_observed') is True,
                    'scope': 'Current owned static preview assertion; not proof of backend persistence or encoded format by itself.'})
        page.update(deferred_browser_refresh_count=deferred, actionable_evidence_count=len(rows)-deferred,
                    ordering_note='Unimplemented or missing evidence first; stale historical browser scenarios last. All current validation and acceptance guards remain mandatory.')
        page['current_browser_evidence'] = browser_proofs[offset:offset + 12]
        page['browser_evidence_next_offset'] = offset + 12 if offset + 12 < len(browser_proofs) else None
        page['browser_evidence_total'] = len(browser_proofs)
        page['browser_evidence_note'] = ('Copy the exact evidence_id only after reviewing its assertion against the criterion. '
            'report_id is a diagnostic report identifier, not an acceptance evidence ID. '
            'Use offset=browser_evidence_next_offset for further available browser proofs. '
            'No browser proof is automatically linked. Stale and failed assertions are excluded.')
        status = self.mapping_status(report)
        unmapped = status['unmapped_requirements']
        if stage_scope:
            page['stage_scope'] = {'stage_id':stage_scope['stage_id'],
                'requirement_count':len(stage_scope['requirement_ids']),
                'criterion_count':len(stage_scope['criteria']),
                'global_unresolved_row_count':len(current['repairs']),
                'global_unmapped_requirement_count':len(unmapped)}
            unmapped = [row for row in unmapped if row['id'] in stage_scope['requirement_ids']]
        page['unmapped_requirements'] = [
            {'id': row['id'], 'text': row['text'][:600], 'text_truncated': len(row['text']) > 600}
            for row in unmapped[:4]]
        if self.catalog and self.catalog.get('review_issues'):
            page.update(next_tool='review_project_requirements',
                        next_action='Review the pending original source blocks before mapping or repairing links.')
        elif rows:
            page.update(next_tool='link_acceptance_evidence',
                        next_action=('Finish the listed missing implementation/assertions before refreshing stale browser scenarios; validate stable source, then collect fresh evidence and link it.' if deferred and len(rows)>deferred else 'Refresh the listed historical browser scenarios on stable validated source, then link current evidence; stale proofs remain invalid.' if deferred else 'Review the listed executed assertions, then submit repairs with these current item_id values.'))
        elif unmapped:
            page.update(next_tool='acceptance_plan',
                        next_action='These requirements have no criterion yet. Save reviewed criteria with their exact IDs and relevant evidence; complete=false, merge=true.')
        elif stage_scope:
            page.update(next_tool='project_plan',
                        next_action='No unresolved link or unmapped requirement is listed in this stage scope. Continue the preserved project plan; this is NOT whole-project completion. All original requirements and global fresh delivery checks remain mandatory.')
        elif status['ready_for_final_response']:
            page.update(next_tool=None, next_action='Return the final answer. Host delivery checks still apply.')
        else:
            page.update(next_tool='validate_project' if not current['validation_current'] else None,
                        next_action='Obtain fresh successful validation before linking evidence.' if not current['validation_current']
                        else 'Inspect the acceptance error before delivery.', error=status.get('error'))
        from project_evidence_repair import route_evidence_page
        page = route_evidence_page(page)
        if repairs is None:
            return page
        if not isinstance(repairs, list) or len(repairs) > 40:
            return {**page, 'error': 'Provide at most 40 explicit link corrections.'}
        if stage_scope and not rows and repairs:
            return {**page, 'saved':False, 'not_executed':True,
                    'error':'No unresolved link targets exist in the current stage. Do not invent or reuse item IDs.',
                    'next_tool':'project_plan',
                    'next_call':{'action':'read', 'stage_id':stage_scope['stage_id']},
                    'repair_feedback':{'batch_saved':False, 'current_item_ids':[],
                        'next_action':'Read the preserved stage criteria and requirement coverage. Map missing criteria using acceptance_plan; do not resave resolved links. All criteria and final delivery checks remain unchanged.'}}
        corrections = []
        for repair in repairs:
            if (not isinstance(repair, dict) or set(repair) != {'item_id', 'old_test', 'new_test'}
                    or not all(isinstance(value, str) for value in repair.values())
                    or repair['item_id'] not in ids):
                return {**page, 'error': 'Copy the FULL current item_id including its colon/hash suffix. Supply only item_id, old_test and new_test.',
                        'repair_feedback': {'batch_saved':False,'current_item_ids':list(ids),'received_item_id':repair.get('item_id') if isinstance(repair,dict) else None,'next_action':'Copy the exact opaque item_id from rows; A7 alone is not A7:hash. Use exact existing_links and current executed candidate/browser IDs. Do not invent node IDs.'}}
            selected = plan['items'][int(ids[repair['item_id']][1:]) - 1]
            if repair['old_test'] not in selected['tests']:
                already_linked = repair['new_test'] in selected['tests']
                if not already_linked:
                    current_row = next(row for row in rows if row['item_id'] == repair['item_id'])
                    return route_evidence_page({**page, 'rows': [current_row],
                            'error': 'old_test must be an exact stored link. Empty strings and review:target diagnostics are not links.',
                            'repair_feedback': {'item_id': repair['item_id'], 'field': 'old_test',
                                'received_old_test': repair['old_test'], 'current_links': list(selected['tests']),
                                'missing_tests': current_row['missing_tests'],
                                'candidate_successful_tests': current_row['candidate_successful_tests'],
                                'observable_review': current_row['observable_review'],
                                'batch_saved': False,
                                'next_action': 'Do not resend this correction. If adding relevant fresh evidence, use an exact current_links entry as old_test; valid links are retained. review:target entries describe missing assertions, not link IDs. If the asserted outcome has no relevant executed candidate, implement/capture that outcome first, then query this tool for fresh IDs. Never invent node:ui-observation or clear a diagnostic by name.'}})
            current_row = next(row for row in rows if row['item_id'] == repair['item_id'])
            invalid = current_row['repairable_existing_links']
            if invalid and repair['old_test'] not in invalid and repair['new_test'] not in selected['tests']:
                return {**page, 'rows':[current_row],
                    'error':'Repair an invalid stored link first; adding beside a valid link leaves the listed defects unresolved.',
                    'repair_feedback':{'batch_saved':False,'item_id':repair['item_id'],'repairable_existing_links':invalid,
                        'next_action':'Use an exact listed invalid old_test and a relevant fresh executed new_test. Valid links and every criterion remain preserved. If no relevant replacement exists, implement/capture that outcome first.'}}
            corrections.append({**repair, 'item_id': ids[repair['item_id']]})
        try:
            result = self.invoke(repair=True, plan_revision=current['plan_revision'], repairs=corrections)
        except ValueError as error:
            affected = {repair['item_id'] for repair in repairs}
            return route_evidence_page({**page, 'rows': [row for row in rows if row['item_id'] in affected][:3] or page['rows'],
                    'error': str(error)})
        return {key: result[key] for key in ('saved', 'link_only_repair', 'changes', 'unchanged',
                                              'validation_current', 'next_action')} | {
            'mapping_status': {'ready_for_final_response': result['mapping_status']['ready_for_final_response']}}

    def invoke(self, items=None, test_query="", test_offset=0, test_limit=24, merge=False, complete=True, repair=False, plan_revision=None, repairs=None, *, recover_revision=False):
        report = self.workspace.last_validation()
        if isinstance(items, list) and len(items) > 40:
            raise ValueError('Save at most 40 rows per call; use complete=false and merge=true for larger catalogs')
        if type(repair) is not bool:raise ValueError("repair must be a boolean")
        if repair:
            if items is not None or merge or test_query or test_offset or not complete:raise ValueError("Link repair cannot be combined with plan/catalog mutations")
            return apply_link_repair(self, report, plan_revision, repairs if repairs is not None else [],
                                     recover_revision=recover_revision)
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
                    'catalog_revision': (self.catalog or {}).get('revision'),
                    'source_review_issues': (self.catalog or {}).get('review_issues', []),
                    'legacy_plan_requires_review': bool(self.catalog and self.path.exists() and not self.load()),
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
                    'scope': 'Project-wide original requirements and referenced specifications; continuation messages are not new obligations.' if self.catalog else 'Current request and current user brief; preserve applicable historical requirements.'}
            if self.catalog and self.catalog.get('pending_obligation_reviews'):
                pending=set(self.catalog['pending_obligation_reviews'])
                sources=[r for r in self.catalog['source_requirements'] if r['id'] in pending][:16]
                result['source_review']={'pending_count':len(pending),'shown':len(sources),
                    'blocks':[{'requirement_id':r['id'],'text':r['text'][:2000],
                        'text_truncated':len(r['text'])>2000,'source_length':len(r['text']),
                        'supported_host_evidence':host_only_requirements([r]).get(r['id'],[])} for r in sources],
                    'instructions':'Review these ORIGINAL requirement texts, not project-file spans. For a semantically reviewed atomic block use reviewed_atomic=true, whole_source=true and evidence_kind, with no segments. Split compound text with exact text fragments or source-relative ranges. Only actual structural headings can be context. Empty supported_host_evidence means use relevant unit/browser behavior assertions, not a guessed host receipt or unrelated test. After reviewing this batch, remaining unreviewed blocks still prevent delivery.'}
            if result['legacy_plan_requires_review']:
                # Show old criteria as review material, never relabel R1 from a
                # continuation as evidence for the original specification.
                result['previous_plan_for_review'] = json.loads(self.path.read_text(encoding='utf-8')).get('items', [])
                result['migration_note'] = 'Review these historical criteria against the stable source IDs; no evidence is automatically reassigned.'
            if fresh and test_query.strip() and not filtered:
                from project_test_suggestions import zero_query_feedback
                result['zero_query_feedback'] = zero_query_feedback(self, report, tests, test_query, priority)
            if self.catalog and len(self.requirements) > 40:
                result['batch_diagnostics'] = self.batch_diagnostics(report, result['mapping_status']['unmapped_requirements'])
            return result
        if merge:
            plan = self.load()
            if not plan or plan.get('version') != 2 or not isinstance(items, list) or not items:
                raise ValueError('merge requires an existing requirement-ID plan and nonempty updates')
            # Merge adds criteria; only an exact criterion/group identity updates
            # an existing row. Shared requirement IDs never delete sibling criteria.
            updates = {}
            identity = lambda row: (row['criterion'], tuple(sorted(set(row['requirement_ids']))))
            for row in items:
                if (not isinstance(row, dict) or set(row)-{'requirement_ids','criterion','tests','quote'}
                        or not isinstance(row.get('requirement_ids'), list) or not row['requirement_ids']
                        or any(not isinstance(key, str) or key not in self.by_id for key in row['requirement_ids'])):
                    raise ValueError('Updates require valid requirement_ids and acceptance row fields')
                self.check_criterion(row)
                key = identity(row)
                if key in updates:
                    if set(updates[key]['tests']) != set(row['tests']):
                        raise ValueError('Conflicting duplicate criterion/group updates; no acceptance rows changed')
                    continue
                updates[key] = dict(row)
            merged = []
            for row in plan['items']:
                update = updates.pop(identity(row), None)
                merged.append({**update, 'requirement_ids': list(row['requirement_ids'])} if update is not None else row)
            items = merged + list(updates.values())
        items = self.check_items(items, require_complete=complete)
        previous = self.load()
        if previous and previous.get("version") == 2:
            covered = {key for row in previous["items"] for key in row.get("requirement_ids", [])}
            if self.catalog or covered == set(self.by_id):preserve_update(previous["items"], items)
        value = {'version': 2 if any('requirement_ids' in row for row in items) else 1,
                 'request_key': self.key, 'items': items}
        if self.catalog:
            value['catalog_revision'] = self.catalog['revision']
            value['requirement_sources'] = {row['id']:row['source_id'] for row in self.requirements}
        if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('Acceptance plan exceeds readable storage size; existing plan preserved')
        unchanged = previous == value
        if not unchanged:
            if self.catalog:
                self.archive_previous_plan()
            atomic_json(self.path, value)
        coverage = self.evaluate(report)
        missing = [{'item': row['id'], 'tests': [t for t in row['missing_tests'] if t != 'host:arabic_response']}
                   for row in coverage['items'] if any(t != 'host:arabic_response' for t in row['missing_tests'])]
        status = self.mapping_status(report)
        result = {'saved': not unchanged, 'unchanged': unchanged, 'items': len(items), 'request_key': self.key,
                'catalog_revision': (self.catalog or {}).get('revision'),
                'missing_evidence': missing, 'validation_current': self.fresh(report),
                'mapping_status': status,
                'repair_proposal': repair_proposal(self, report),
                'next_action': 'Repair only reported or unmapped rows with merge=true.' if missing or coverage.get('error') else 'Deliver the final answer now; final host checks remain mandatory. Do not repeat catalog queries or validation without a source change or actual failure.',
                'note': 'Final delivery checks fresh evidence and the final response; host:arabic_response is evaluated then.'}
        if status['unmapped_requirements']:
            result['next_action'] = 'Draft saved. Map only the next unmapped requirements with complete=false and merge=true. Unmapped requirements still block delivery.'
            if self.fresh(report):
                result['successful_tests'] = list(dict.fromkeys(
                    test for check in report.get('checks', []) if check.get('passed')
                    and check.get('validation_run_id') and check.get('executed', 0) > 0
                    for test in check.get('successful_tests', [])))[:16]
            result['batch_diagnostics'] = self.batch_diagnostics(report, status['unmapped_requirements'])
            partial = self.evaluate(report, allow_partial=True)
            if self.fresh(report):
                result['verified_batch_ids'] = list(dict.fromkeys(
                    key for row in partial.get('items', []) if row['passed']
                    for key in row.get('requirement_ids', [])))
        if self.catalog and self.catalog.get('review_issues'):
            result['source_review_issues'] = self.catalog['review_issues']
            result['next_action'] = 'Use review_project_requirements with the current revision and source_requirements. Correct review metadata before mapping acceptance; use whole_source=true only for explicitly reviewed atomic blocks. Preserve passing project files and tests.'
        if missing:
            result['evidence_repair'] = evidence_repair(report, missing, self.fresh(report))
            result['next_step'] = self.repair_message(report, coverage)
        if unchanged:
            result['next_action'] = 'No acceptance plan change was saved. Do not resubmit this identical row. Review the current execution stage and implement/test its missing behavior or map a genuinely unmapped relevant criterion. All prior criteria and links remain unchanged.'
        return result

    def mapping_status(self, report):
        """Bounded actionable defects; no inferred success or automatic behavior mapping."""
        coverage = self.evaluate(report, allow_partial=True)
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
            unranked = (not candidates and not criterion_requirements(row['criterion'])
                        and not row.get('target_reference_review', {}).get('observable_review', {}).get('missing'))
            if unranked:candidates = successful[:6]
            defects.append({'item_id':row['id'], 'requirement_ids': row.get('requirement_ids', []), 'missing_tests': missing,
                            'candidate_successful_tests': candidates,
                            'candidates_are_unranked_examples':unranked,
                            'requires_assertion_review':True,
                            'target_reference_review':row.get('target_reference_review',{})})
        plan=self.load()
        covered={key for row in (plan or {}).get('items',[]) for key in row.get('requirement_ids',[])}
        unmapped = [row for row in self.requirements if row['id'] not in covered]
        return {'missing_plan': coverage.get('missing_plan', False), 'error': coverage.get('error'),
                'unmapped_requirements':unmapped,
                'defects': defects, 'final_response_pending': any('host:arabic_response' in row['missing_tests'] for row in coverage.get('items', [])),
                'ready_for_final_response': bool(coverage.get('mandatory_named_tests_executed')) and not unmapped and not defects and not coverage.get('missing_plan') and not coverage.get('error') and self.fresh(report)}

    def batch_diagnostics(self, report, rows):
        from project_test_suggestions import nearby_tests
        fresh = self.fresh(report)
        tests = [test for check in (report or {}).get('checks', [])
                 if check.get('passed') and check.get('validation_run_id') and check.get('executed', 0) > 0
                 for test in check.get('successful_tests', [])] if fresh else []
        pending = set((self.catalog or {}).get('pending_obligation_reviews', []))
        diagnostics = []
        for row in rows[:8]:
            needs_review = row.get('source_requirement_id', row['id']) in pending
            candidates = [] if needs_review else nearby_tests(tests, report, row['text'])[:2]
            reason = ('source_review_required' if needs_review else 'fresh_validation_required' if not fresh else
                      'review_executed_candidates_and_link' if candidates else 'no_ranked_test_candidate')
            diagnostics.append({'id': row['id'], 'reason': reason,
                                'executed_test_candidates': candidates,
                                'requires_assertion_review': bool(candidates)})
        return diagnostics

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
        if not isinstance(items, list) or not items or (not self.catalog and len(items) > 40):
            raise ValueError('Acceptance plan requires 1..40 items')
        if self.catalog and any(not isinstance(row, dict) or not row.get('requirement_ids') for row in items):
            raise ValueError('Project catalog mappings require exact stable requirement_ids; legacy quotes must be reviewed')
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
            if not self.catalog and len(normalized) > 40:
                raise ValueError('Acceptance plan exceeds 40 items including delivery constraints')
            missing = [key for key in self.by_id if key not in covered]
            if missing and require_complete:
                if len(missing) > 8:
                    raise ValueError(f'Unmapped requirement IDs: {len(missing)} remain. '
                                     'Save reviewed rows with complete=false, then add batches with merge=true. '
                                     'Query acceptance_plan for the next missing batch.')
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

    def evaluate(self, report, *, allow_partial=False):
        if self.catalog and self.catalog.get('review_issues'):
            return {'passed':False, 'items':[], 'error':'Requirement sources need review: ' +
                    '; '.join(self.catalog['review_issues'])}
        plan = self.load()
        if not plan:
            return {'passed': False, 'missing_plan': True, 'items': [], 'scope': 'Current request and current brief'}
        try:
            items = self.check_items(plan['items'], require_complete=not (allow_partial and self.catalog))
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
            for key in row.get('requirement_ids',[]):
                obligation=next((r for r in self.requirements if r['id']==key),{})
                if 'evidence_kind' not in obligation:
                    continue
                kind=obligation.get('evidence_kind','behavior')
                prefixes={'unit':('python:','node:'),'browser':('browser:',),
                          'behavior':('python:','node:','browser:'),'host':('host:',)}[kind]
                if not any(test.startswith(prefixes) for test in row['tests']):
                    missing.append('review:evidence-kind:'+key+':'+kind)
            rows.append({'id': f'A{i+1}', **row, 'passed': not missing, 'target_reference_review':binding,
                         'required_evidence': required, 'missing_links': missing_links, 'missing_tests': missing,
                         'primary_host_evidence': {t:receipts[t] for t in required if t in receipts},
                         'named_tests_require_assertion_review': [t for t in row['tests'] if t.startswith(('python:','node:'))],
                         'evidence': {t: receipts[t] for t in row['tests'] if t in receipts}})
        covered = {key for row in rows for key in row.get('requirement_ids', [])}
        complete = not self.catalog or covered == set(self.by_id)
        return {'passed': complete and tests_executed and bool(rows) and all(r['passed'] for r in rows) and self.fresh(report), 'items': rows,
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
        'host:targeted_source_patch': targeted_patch_evidence(workspace,initial_tests,job),
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
    if job.get('comment_preservation_required'):
        from project_comment_evidence import evidence as comment_evidence
        result['host:source_comments_preserved']=comment_evidence(workspace,job)
    for name, proof in (browser or {}).items():
        result[name] = {**proof, 'observed_passed': proof.get('passed') is True,
                        'passed': bool(proof.get('passed') and proof.get('fingerprint') == fingerprint)}
    return result
