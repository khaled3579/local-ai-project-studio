"""Exact, fresh receipt suggestions; never infer or replace acceptance links."""
from urllib.parse import unquote


def evidence_repair(report, missing, fresh):
    if not fresh:
        return {'validation_current': False, 'items': [], 'action': 'Validate current sources before repairing evidence links.'}
    receipts = {}
    node_receipts = []
    for check in (report or {}).get('checks', []):
        if not check.get('passed') or not check.get('validation_run_id') or check.get('executed', 0) <= 0:
            continue
        identities = check.get('test_identities') or {}
        for name in check.get('successful_tests', []):
            legacy = identities.get(name, {}).get('legacy', name)
            receipts.setdefault(legacy, []).append(name)
            identity = identities.get(name, {})
            if name.startswith('node:') and all(isinstance(identity.get(key), str) for key in ('file', 'suite', 'name')):
                node_receipts.append((name, identity))
    unknown = list(dict.fromkeys(name for row in missing for name in row.get('missing_tests', row.get('tests', []))
                                 if name.startswith(('node:', 'python:'))))
    items = []
    for name in unknown[:24]:
        matches = list(dict.fromkeys(receipts.get(name, [])))
        reason = 'ambiguous_leaf' if len(matches) > 1 else 'scoped_identity' if matches else 'not_executed_or_unknown'
        # A scoped spelling may refer to a uniquely named test whose canonical
        # receipt deliberately retains its legacy leaf ID. Compare all three
        # decoded identity fields, never a suffix or an inferred test title.
        if not matches and name.startswith('node:'):
            scope = name[5:].split('::')
            if len(scope) == 3:
                file, suite, leaf = (unquote(part) for part in scope)
                matches = list(dict.fromkeys(test for test, identity in node_receipts
                    if (identity['file'], identity['suite'], identity['name']) == (file, suite, leaf)))
                if matches:
                    reason = 'canonical_executed_id'
                else:
                    # These are explicitly candidates for review, not aliases:
                    # the supplied title/suite may be invented. An exact file
                    # match bounds the catalog to the useful executed tests.
                    matches = list(dict.fromkeys(test for test, identity in node_receipts if identity['file'] == file))
                    if matches:reason = 'file_candidates_title_or_suite_not_matched'
        items.append({'missing_test': name, 'matching_successful_tests': matches[:6], 'total_matches': len(matches),
                      'reason': reason, 'requires_review': True})
    return {'validation_current': True, 'items': items, 'total_missing': len(unknown),
            'action': 'Evidence IDs are opaque: copy a returned canonical ID verbatim; never construct or encode a scope yourself. '
                      'Review file, suite and assertion relevance and update the relevant plan rows using these exact executed IDs. '
                      'Do not revalidate unchanged passing sources to repair an ID. Unknown IDs require inspecting actual tests; '
                      'no suggestion grants acceptance and no link has been changed.'}
