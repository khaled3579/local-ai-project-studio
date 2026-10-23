"""Typed host receipts and atomic link repairs. No fuzzy ID is auto-approved."""
from collections import Counter
import hashlib
import json
import re

FULL_SUITE = 'host:full_test_suite_passed'
PERFORMANCE = 'host:performance_contract_passed'
PROTECTED = 'host:protected_files_unchanged'


def typed_requirements(text):
    """Recognize explicit evidence obligations, not arbitrary behavior semantics."""
    text = text.casefold()
    result = []
    if re.search(r'(?:entire|full|all|unchanged).{0,90}(?:original|upstream|unittest|npm|node:test|typescript).{0,90}(?:suite|tests?|script)|(?:run|execute).{0,70}(?:entire|full|unchanged).{0,70}(?:suite|npm test)', text):
        result.append(FULL_SUITE)
    if re.search(r'(?:preserv\w*|no slower|do not replace).{0,110}(?:runtime performance|algorithmic|per-item loop|bulk implementation)|performance preserved|no slower per-item', text):
        result.append(PERFORMANCE)
    if re.search(r'(?:preserve|retain|unchanged|preserved).{0,60}exact bytes|exact bytes.{0,60}preserved', text):
        result.append(PROTECTED)
    return result


def criterion_requirements(text):
    result = typed_requirements(text)
    if PROTECTED not in result and re.search(r'(?:test\.py|adapter|(?:existing|original) tests?|readme).{0,80}(?:preserved unchanged|not edited|unchanged)|(?:preserve|do not edit).{0,80}(?:existing tests|original tests|readme|test\.py)', text.casefold()):
        result.append(PROTECTED)
    return result


def typed_only(text):
    clause = text.strip().rstrip('.;؛!?؟').strip().casefold()
    # Full-clause allowlist. Mixed functional requests still need explicit tests.
    patterns = (
        r'run the entire original unittest suite through the provided [\w./-]+ discovery adapter',
        r'run the unchanged npm test script including all original node:test tests and typescript type tests',
        r'preserve algorithmic runtime performance by reusing existing optimized helpers where appropriate',
        r'do not replace a bulk implementation with a slower per-item loop',
    )
    return typed_requirements(clause) if any(re.fullmatch(pattern, clause) for pattern in patterns) else []


def receipts(owner, report):
    fresh = owner.fresh(report)
    checks = (report or {}).get('checks', [])
    suites = [row for row in checks if row.get('name') in {'python_unit_tests','npm_test','web_node_tests'}]
    full = bool(fresh and suites and all(row.get('passed') and row.get('validation_run_id')
                and row.get('executed', 0) > 0 and row.get('successful_tests') for row in suites))
    performance = [row for row in checks if row.get('name') == 'performance_contract']
    contract = getattr(owner.workspace, 'performance_contract', None)
    perf = bool(fresh and contract and performance and all(row.get('passed') and row.get('validation_run_id')
                and row.get('contract_sha256') == contract['policy_sha256'] and row.get('measurements')
                and all(item.get('passed') is True for item in row['measurements']) for row in performance))
    protected = all(owner.evidence.get(name, {}).get('passed') is True for name in
                    ('host:original_tests_unchanged', 'host:original_readmes_unchanged'))
    def proof(passed, rows, scope):
        return {'passed': passed, 'fingerprint': (report or {}).get('fingerprint'),
                'validation_run_ids': [row['validation_run_id'] for row in rows if row.get('validation_run_id')],
                'evidence': [row['evidence'] for row in rows if row.get('evidence')], 'scope': scope}
    return {FULL_SUITE: proof(full, suites, 'Fresh complete configured test commands, including their chained type tests; individual test names are not suite receipts.'),
            PERFORMANCE: proof(perf, performance, 'Fresh host-pinned performance contract with unchanged reference and thresholds; a test whose name contains performance is not a benchmark.'),
            PROTECTED: proof(protected, [], 'Both protected original test and README snapshots retain their exact bytes; no caller assertion is accepted.')}


def revision(owner, report):
    value = {'plan': owner.load(), 'source': owner.workspace.fingerprint(force=True),
             'runs': [row.get('validation_run_id') for row in (report or {}).get('checks', [])],
             'performance': (report or {}).get('performance_contract_sha256')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def preserve_rows(old, new):
    """A link correction cannot remove a prior behavior hidden in the same group."""
    key = lambda row: (row['criterion'], tuple(row.get('requirement_ids', [])), row.get('quote',''))
    wanted = Counter(key(row) for row in old)
    actual = Counter(key(row) for row in new)
    lost = wanted - actual
    if lost:
        raise ValueError('Acceptance repair would remove existing criteria or requirement groups. Use repair=true with the current plan_revision for link-only updates. Missing criteria: '
                         + json.dumps([item[0] for item in lost], ensure_ascii=False))


def preserve_update(old, new):
    """Keep legacy group refinement; forbid dropping requirements or sibling behaviors."""
    old_ids = {key for row in old for key in row.get('requirement_ids', [])}
    new_ids = {key for row in new for key in row.get('requirement_ids', [])}
    if not old_ids <= new_ids:
        raise ValueError('Acceptance update would remove existing requirement groups')
    groups = Counter(tuple(row.get('requirement_ids', [])) for row in old)
    protected = [row for row in old if groups[tuple(row.get('requirement_ids', []))] > 1]
    preserve_rows(protected, new)


def host_only_row(owner, row):
    """Only whole authoritative clauses may bypass behavioral target matching."""
    from project_acceptance import host_only_requirements
    requirements = getattr(owner, 'requirements', [])
    ids = row.get('requirement_ids', [])
    if ids:
        supported = host_only_requirements(requirements, owner.capture_tests())
        return all(key in supported for key in ids)
    # Legacy quote plans have no IDs; use only the existing whole-clause grammar.
    clauses = [{'id': 'legacy', 'text': row.get('quote', '')}]
    return 'legacy' in host_only_requirements(clauses, owner.capture_tests())


def proposal(owner, report):
    plan = owner.load()
    coverage = owner.evaluate(report)
    repairs = []
    known = list(dict.fromkeys(name for check in (report or {}).get('checks', [])
                 if check.get('passed') and check.get('validation_run_id') and check.get('executed', 0) > 0
                 for name in check.get('successful_tests', []))) if owner.fresh(report) else []
    host = {name for name, proof in {**owner.evidence, **receipts(owner, report)}.items() if proof.get('passed') is True}
    for row in coverage.get('items', []):
        typed = criterion_requirements(row['criterion'])
        if not typed:
            typed = [name for name in typed_requirements(row['quote']) if name != PROTECTED]
        missing = [name for name in row['missing_tests'] if name != 'host:arabic_response']
        if not missing:
            continue
        auto = []
        from project_acceptance_targets import review_candidates
        binding = row.get('target_reference_review', {})
        candidates = review_candidates(owner, report, row, known)
        for name in missing:
            # A full-suite receipt cannot stand in for a named behavioral target.
            # Pure typed obligations retain the existing automatic atomic repair.
            if name.startswith(('node:', 'python:')) and typed and typed[0] in host and host_only_row(owner, row) and not binding.get('missing_targets'):
                auto.append({'old_test': name, 'new_test': typed[0], 'basis': 'required typed host receipt, never a guessed test name'})
        repairs.append({'item_id': row['id'], 'requirement_ids': row.get('requirement_ids', []),
                        'criterion': row['criterion'], 'missing_tests': missing,
                        'target_reference_review': binding,
                        'candidate_successful_tests': candidates,
                        'requires_assertion_review': True,
                        'reviewed_repair_format': [{'item_id': row['id'], 'old_test': name, 'new_test': candidates[0]}
                                                  for name in missing if candidates and name.startswith(('python:', 'node:')) and name not in known],
                        'guidance': 'Review the actual callback/method of these exact executed IDs. Discovery load_tests hooks are not test IDs. '
                                    'For behavioral targets use an executed behavioral test, not host:full_test_suite_passed. '
                                    'Candidates and example arguments are suggestions, never automatic behavioral approval.',
                        'automatic_replacements': auto})
    return {'plan_revision': revision(owner, report), 'validation_current': owner.fresh(report),
            'missing_plan': not bool(plan), 'repairs': repairs,
            'action': 'Review these row-specific replacements. Call acceptance_plan(repair=true, plan_revision=...). '
                      'For an unknown behavioral ID, supply repairs=[{item_id,old_test,new_test}] with a relevant executed ID reviewed against the assertion. '
                      'No criterion, requirement group, or valid executed link is removed. A valid old_test is retained when adding missing evidence; an already satisfied repair is a no-op. No validation rerun is needed for link repair. '
                      'Typed suite, performance, and preservation receipts are mandatory for their obligations; a similarly named test cannot substitute.'}


def apply(owner, report, ticket, updates):
    from app_maintenance import atomic_json
    plan = owner.load()
    if not plan or not owner.fresh(report):
        raise ValueError('Link repair requires an existing plan and fresh successful validation; no files were changed')
    if not isinstance(ticket, str) or ticket != revision(owner, report):
        raise ValueError('Stale or missing plan_revision; read the current acceptance repair proposal. No files were changed')
    if not isinstance(updates, list) or len(updates) > 40:
        raise ValueError('repairs must be a list of at most 40 explicit row corrections')
    proposed = proposal(owner, report)
    current = json.loads(json.dumps(plan))
    known = {name for row in report['checks'] if row.get('passed') and row.get('validation_run_id')
             and row.get('executed', 0) > 0 for name in row.get('successful_tests', [])}
    host = {name for name, proof in {**owner.evidence, **receipts(owner, report)}.items() if proof.get('passed') is True}
    changes = []
    preserved = []
    touched = set()
    manual = {}
    for update in updates:
        if not isinstance(update, dict) or set(update) != {'item_id','old_test','new_test'} or not all(isinstance(value, str) for value in update.values()):
            raise ValueError('Each correction requires string item_id, old_test, new_test only')
        key = (update['item_id'], update['old_test'])
        if key in manual and manual[key] != update['new_test']:
            raise ValueError('Conflicting explicit corrections for the same row/link; no files were changed')
        manual[key] = update['new_test']
    automatic = [{'item_id': row['item_id'], 'old_test': item['old_test'], 'new_test': item['new_test']}
                 for row in proposed['repairs'] for item in row['automatic_replacements']
                 if (row['item_id'], item['old_test']) not in manual]
    explicit = [{'item_id':key[0], 'old_test':key[1], 'new_test':new} for key,new in manual.items()]
    for update in automatic + explicit:
        if not isinstance(update, dict) or set(update) != {'item_id','old_test','new_test'}:
            raise ValueError('Each correction requires item_id, old_test, new_test only')
        item_id, old, new = update['item_id'], update['old_test'], update['new_test']
        if not isinstance(item_id, str) or not re.fullmatch(r'A[1-9]\d*', item_id):
            raise ValueError('Invalid acceptance item_id')
        index = int(item_id[1:]) - 1
        if index >= len(current['items']) or not isinstance(old, str) or not isinstance(new, str):
            raise ValueError('Invalid row or link')
        if new not in known | host:
            raise ValueError('Replacement must be an exact fresh executed ID or passing typed host receipt: ' + new)
        row = current['items'][index]
        if old not in row['tests']:
            if new in row['tests']:
                continue
            raise ValueError('Old link is not in the selected item; refusing a stale correction')
        if old in known | host:
            if new in row['tests']:
                preserved.append({'item_id':item_id, 'test':old, 'reason':'Requested evidence already linked; existing executed link retained. Read mapping_status for any remaining defects.'})
                continue
            candidate = {**row, 'tests': list(dict.fromkeys(row['tests'] + [new]))}
            mode = 'add_preserving_valid_link'
        else:
            candidate = {**row, 'tests': list(dict.fromkeys(new if name == old else name for name in row['tests']))}
            mode = 'replace_invalid_link'
        row['tests'] = candidate['tests']
        touched.add(index)
        changes.append({'item_id':item_id, 'old_test':old, 'new_test':new, 'mode':mode})
    items = owner.check_items(current['items'])
    preserve_rows(plan['items'], items)
    # References may be supplied by different corrections in the same row.
    # Evaluate the complete staged transaction, never an intermediate row.
    from project_acceptance_targets import relevance, review_candidates
    unresolved = []
    for index in sorted(touched):
        row = items[index]
        remaining = relevance(owner,report,row)['missing_targets']
        if remaining:
            unresolved.append({'item_id':f'A{index+1}', 'missing_targets':remaining,
                               'candidate_successful_tests':review_candidates(owner,report,row,known)})
    if unresolved:
        raise ValueError('Repair batch does not reference the explicit code target in every changed criterion; no files changed. '
                         'All corrections were reviewed together; a full-suite receipt is not behavioral evidence. '
                         + json.dumps({'unresolved_rows':unresolved}, ensure_ascii=False))
    # Recheck freshness after building the whole transaction, before committing.
    if ticket != revision(owner, report) or not owner.fresh(report):
        raise ValueError('Source or evidence changed during link repair; no files were changed')
    current['items'] = items
    if current != plan:
        atomic_json(owner.path, current)
    return {'saved': True, 'link_only_repair': True, 'changes': changes,
            'preserved_valid_links': preserved, 'unchanged': current == plan,
            'plan_revision': revision(owner, report), 'validation_current': True,
            'mapping_status': owner.mapping_status(report),
            'next_action': 'Deliver now if ready_for_final_response; otherwise repair only the remaining explicit defects. Source files and tests were not edited.'}
