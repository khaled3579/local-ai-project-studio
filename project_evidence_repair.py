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


def route_evidence_page(page):
    """Do not propose relinking when the missing observable has no fresh proof."""
    if not page.get('validation_current') or page.get('next_tool') == 'review_project_requirements':
        return page
    first = next(iter(page.get('rows') or []), None)
    if not first or first.get('candidate_successful_tests') or first.get('automatic_replacements'):
        return page
    if first.get('evidence_state') == 'refresh_after_source_stable':
        return {**page, 'next_tool':'browser_project', 'evidence_required_before_linking':True}
    missing = (first.get('observable_review') or {}).get('missing') or []
    if missing:
        return {**page, 'next_tool': 'browser_project' if any(v.startswith('ui-') or v in {'webp-format','decoded-webp-output'} for v in missing) else None,
                'evidence_required_before_linking': True,
                'evidence_focus': {'item_id': first['item_id'], 'missing': missing},
                'next_action': 'Do NOT call link_acceptance_evidence again on unchanged evidence. No suitable executed candidate proves the listed missing observable. Implement the missing behavior/test if needed. For UI proof use browser_project on the actual owned page: inspect its snapshot, perform the relevant interaction, then assert changed DOM state or actual output. Open the page only if no usable browser session exists. Visibility alone cannot prove interaction or persistence. For data/file outcomes create a relevant named assertion and validate changed source. After fresh proof exists, query exact IDs and link while preserving current_links and all criteria.'}
    return page


def focused_browser_ids(page):
    """Offer typed receipts for the current missing outcome, not structure-only IDs."""
    rows = page.get('rows') or []
    missing = (page.get('evidence_focus') or {}).get('missing') or (((rows[0].get('observable_review') or {}).get('missing') or []) if rows else [])
    focus = next((kind for kind in ['persisted-mutation','decoded-webp-output','webp-format','ui-interaction'] if kind in missing),None)
    result = []
    for proof in page.get('current_browser_evidence', []):
        interactive = (proof.get('source') in {'text','value','checked','count','dialog_count','image_format'}
                       and proof.get('action') in {'click','fill','select','press','upload'} and proof.get('state_changed') is True)
        if focus == 'ui-interaction' and not interactive:
            continue
        if focus == 'persisted-mutation' and not (interactive and proof.get('mutation_observed') is True):
            continue
        if focus in {'decoded-webp-output','webp-format'}:
            image = proof.get('image_format') or {}
            if not (proof.get('source') == 'image_format' and proof.get('actual') == 'image/webp'
                    and image.get('mime') == 'image/webp' and image.get('signature') == 'RIFF/WEBP'
                    and image.get('decoded') is True and type(image.get('bytes')) is int and image['bytes'] >= 20):
                continue
        result.append(proof['evidence_id'])
    return result


def exact_link_schema(tools, page):
    """Constrain spelling to current host IDs, without granting evidence relevance."""
    import copy
    result = copy.deepcopy(tools)
    rows = page.get('rows') or []
    item_ids = list(dict.fromkeys(row['item_id'] for row in rows))
    old = list(dict.fromkeys(value for row in rows for value in (row.get('repairable_existing_links') or row.get('existing_links', []))))
    new = list(dict.fromkeys([value for row in rows for value in row.get('candidate_successful_tests', [])]
        + [entry['new_test'] for row in rows for entry in row.get('automatic_replacements', [])]
        + focused_browser_ids(page)))
    for entry in result:
        if entry['function']['name'] != 'link_acceptance_evidence':
            continue
        missing = (page.get('evidence_focus') or {}).get('missing') or (((rows[0].get('observable_review') or {}).get('missing') or []) if rows else [])
        if not rows or (missing and not new):
            entry['function']['parameters']['properties'].pop('repairs',None)
            entry['function']['description'] += ' No unresolved target is visible on this page; follow next_tool or query another offset. Never invent item IDs. No suitable typed ID is visible on this page; query only or collect the missing actual proof. Use offset to discover more current receipts. No criterion or link changed.'
            continue
        repair_schema = entry['function']['parameters']['properties']['repairs']['items']
        # The old link must belong to the chosen row, not merely occur in the
        # same page. Independent enums admit invalid cross-row combinations.
        alternatives = []
        for row in rows:
            row_old = row.get('repairable_existing_links') or row.get('existing_links', [])
            alternatives.append({'type':'object', 'properties':{
                'item_id':{'type':'string','enum':[row['item_id']]},
                'old_test':{'type':'string','enum':list(dict.fromkeys(row_old))},
                'new_test':{'type':'string', **({'enum':new} if new else {})}},
                'required':['item_id','old_test','new_test'], 'additionalProperties':False})
        repair_schema['anyOf'] = alternatives
        entry['function']['description'] += ' Each old_test must belong to the selected item_id in the same row. Never combine IDs from different rows; valid stored links remain preserved.'
        fields = repair_schema['properties']
        for name, values in [('item_id',item_ids),('old_test',old),('new_test',new)]:
            if values:
                fields[name]['enum'] = values
    return result


def enforce_scoped_link_call(choice, tools):
    """Validate only scoped linking; unrelated and legacy routing stay unchanged."""
    import json
    from project_generation import ActionFormatError, validate_action_value
    for call in (choice.get('message') or {}).get('tool_calls') or []:
        function = call.get('function') or {}
        if function.get('name') != 'link_acceptance_evidence':
            continue
        offered = next((entry['function'] for entry in tools
                        if entry['function']['name'] == 'link_acceptance_evidence'), None)
        if offered is None:
            raise ActionFormatError('Linking unavailable in this stage. Follow the current project_plan step.')
        try:
            args = json.loads(function.get('arguments') or '{}')
        except (ValueError, TypeError):
            raise ActionFormatError('Link arguments must be complete JSON') from None
        validate_action_value(args, offered['parameters'])
