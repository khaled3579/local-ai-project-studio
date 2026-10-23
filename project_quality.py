"""Evidence for delivery. Passing checks never imply all requirements are covered."""
import hashlib
import time
from project_validation import VERSION


def delivery_evidence(workspace, prompt, report, *, requirements_reviewed=False, acceptance=None):
    from project_source_guard import validation_current as source_current
    from project_performance import contract_current
    performance = getattr(workspace, 'performance_contract', None)
    quality = getattr(workspace, 'quality_contract', None)
    fingerprint = workspace.fingerprint(force=True)
    current = bool(report and report.get('fingerprint') == fingerprint and
                   report.get('validator_version') == VERSION and
                   report.get('source_unchanged_during_validation') and not report.get('in_progress')
                   and not getattr(workspace, 'validation_pending', False)
                   and report.get('performance_contract_sha256') == (performance or {}).get('policy_sha256')
                   and contract_current(performance)
                   and report.get('quality_contract_sha256') == (quality or {}).get('policy_sha256')
                   and contract_current(quality) and source_current(workspace, report))
    checks = [{k: row[k] for k in ('name', 'passed', 'tests', 'tests_passed', 'executed', 'skipped') if k in row}
              for row in (report or {}).get('checks', [])]
    coverage = acceptance.evaluate(report) if acceptance is not None else None
    return {'version': 2, 'created': time.time(), 'request_sha256': hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
            'fingerprint': fingerprint, 'validation_current': current,
            'validated': bool(current and report.get('success') and checks and all(c.get('passed') for c in checks) and (coverage is None or coverage['passed'])),
            'requirements_review_requested': requirements_reviewed,
            'requirements_coverage': coverage or 'Not inferred from passing tests; no acceptance mapping requested by this caller.',
            'checks': checks, 'files': workspace.files(),
            'scope': (report or {}).get('scope', 'No validation evidence')}
