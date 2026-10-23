"""Versioned validation receipts and scoped instrumentation of real test runners."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import uuid
import ast

VERSION = 7
COMMAND_ENV = ContextVar('project_validation_command_environment', default={})
ROOT = Path(__file__).resolve().parent


def current_passing_validation(workspace, not_before=None):
    """Reuse executed evidence only for identical sources and this validator version."""
    from project_performance import contract_current
    from project_source_guard import validation_current as source_current
    contract=getattr(workspace,'performance_contract',None)
    quality=getattr(workspace,'quality_contract',None)
    report = workspace.last_validation()
    if (report and report.get('success') and report.get('validator_version') == VERSION
            and (not_before is None or isinstance(report.get('timestamp'), (int, float)) and report['timestamp'] >= not_before)
            and report.get('source_unchanged_during_validation') and not workspace.validation_pending
            and report.get('performance_contract_sha256')==(contract or {}).get('policy_sha256') and contract_current(contract)
            and report.get('quality_contract_sha256')==(quality or {}).get('policy_sha256') and contract_current(quality)
            and source_current(workspace, report)
            and not report.get('in_progress') and report.get('fingerprint') == workspace.fingerprint(force=True)):
        return report
    return None


@contextmanager
def command_environment(values):
    token = COMMAND_ENV.set(values)
    try:
        yield
    finally:
        COMMAND_ENV.reset(token)


def new_run(workspace):
    run_id = uuid.uuid4().hex
    directory = workspace.meta / 'validation-runs' / run_id
    if not directory.resolve().is_relative_to(workspace.meta.resolve()):
        raise ValueError('Invalid validation receipt directory')
    directory.mkdir(parents=True)
    return run_id, directory


def receipt(path, run_id, runner):
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Invalid validation receipt')
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('run_id') != run_id or value.get('runner') != runner:
        raise ValueError('Validation receipt does not match this execution')
    return value


def test_counts(value):
    fields = ('tests', 'executed', 'passed', 'failed', 'skipped')
    for field in fields:
        if type(value.get(field)) is not int or value[field] < 0:
            raise ValueError('Invalid test counts in validation receipt')
    for field in ('errors', 'cancelled', 'todo', 'expected_failures', 'unexpected_successes'):
        if type(value.get(field, 0)) is not int or value.get(field, 0) < 0:
            raise ValueError('Invalid test status in validation receipt')
    if not value['passed'] <= value['executed'] <= value['tests']:
        raise ValueError('Inconsistent test execution counts')
    return {k: value.get(k, 0) for k in (*fields, 'errors', 'cancelled', 'todo', 'expected_failures', 'unexpected_successes')}


def confirmed(counts, success):
    return (success is True and counts['executed'] > 0 and counts['passed'] > 0
            and not any(counts.get(k, 0) for k in ('failed', 'errors', 'cancelled', 'unexpected_successes')))


def python_tests(workspace):
    run_id, directory = new_run(workspace)
    path = directory / 'unittest.json'
    result = workspace.command(['python', str(ROOT / 'validation_python_runner.py'), '--root', str(workspace.root),
                                '--report', str(path), '--run-id', run_id], 120)
    check = {'name': 'python_unit_tests', **result, 'passed': False, 'tests': 0, 'executed': 0,
             'validation_run_id': run_id, 'evidence': str(path.relative_to(workspace.root))}
    try:
        value = receipt(path, run_id, 'unittest')
        counts = test_counts(value)
        ids = value.get('successful_tests', [])
        check['successful_tests'] = [t for t in ids if ids.count(t) == 1]
        check.update({k: v for k, v in counts.items() if k != 'passed'}, tests_passed=counts['passed'])
        check['passed'] = result['exit_code'] == 0 and confirmed(counts, value.get('success'))
        if not check['passed']:
            check['error'] = ('لم تُنفّذ اختبارات Python ناجحة فعلًا؛ الاختبارات المتخطّاة أو المتوقعة الفشل لا تكفي.'
                              if counts['passed'] == 0 else 'فشلت اختبارات Python؛ أصلح الأخطاء دون تخفيف الاختبارات.')
    except (OSError, ValueError, KeyError, TypeError) as error:
        check['error'] = 'تعذّر إثبات نتيجة مشغّل unittest لهذا الفحص: ' + str(error)
    return check


def tkinter_startup(workspace, files):
    """Supplement unit tests with actual construction of detected Tk entry points."""
    checks=[]
    for item in files:
        if not item['path'].endswith(('.py','.pyw')) or any(p in {'tests','test'} for p in Path(item['path']).parts):
            continue
        source=workspace.path(item['path']).read_text(encoding='utf-8-sig')
        tree=ast.parse(source)
        uses_tk=any((isinstance(n,ast.Import) and any(a.name=='tkinter' for a in n.names)) or
                    (isinstance(n,ast.ImportFrom) and (n.module or '').startswith('tkinter')) for n in ast.walk(tree))
        if not uses_tk or '__main__' not in source:continue
        run_id,directory=new_run(workspace);path=directory/'gui-startup.json'
        result=workspace.command(['python',str(ROOT/'validation_gui_runner.py'),'--entry',str(workspace.path(item['path'])),
                                  '--report',str(path),'--run-id',run_id],20)
        check={'name':'python_gui_startup','entry':item['path'],**result,'passed':False,'validation_run_id':run_id}
        try:
            value=receipt(path,run_id,'tkinter:startup')
            check.update(passed=result['exit_code']==0 and value.get('success') is True,windows=value.get('windows',0))
            if not check['passed']:check['error']=value.get('error') or 'Tkinter startup failed'
        except (OSError,ValueError,KeyError) as error:check['error']='GUI startup could not be verified: '+str(error)
        checks.append(check)
    return checks


def node_syntax(workspace, files):
    names = [item['path'] for item in files if item['path'].endswith(('.js', '.mjs', '.cjs'))]
    if not names:
        return {'name': 'web_javascript_syntax', 'passed': True, 'files': 0, 'errors': []}
    run_id, directory = new_run(workspace)
    path = directory / 'syntax.json'
    spec = directory / 'syntax-input.json'
    spec.write_text(json.dumps({'root': str(workspace.root), 'files': names, 'run_id': run_id, 'report': str(path)}), encoding='utf-8')
    result = workspace.command(['node', str(ROOT / 'validation_node_runner.cjs'), str(spec)], 120)
    try:
        value = receipt(path, run_id, 'node:syntax')
        return {'name': 'web_javascript_syntax', 'passed': result['exit_code'] == 0 and value.get('success') is True,
                'files': len(names), 'errors': value['errors'], 'validation_run_id': run_id}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'name': 'web_javascript_syntax', 'passed': False, 'files': len(names),
                'errors': [{'error': str(error), 'stderr': result.get('stderr', '')}]}


def node_tests(workspace, argv, name):
    run_id, directory = new_run(workspace)
    reporter = (ROOT / 'validation_node_runner.cjs').as_uri()
    options = os.environ.get('NODE_OPTIONS', '')
    # Keep human-readable TAP output, and persist independent machine-readable counts.
    options += ' --test-reporter=tap --test-reporter=' + reporter
    options += ' --test-reporter-destination=stdout --test-reporter-destination=stdout'
    with command_environment({'NODE_OPTIONS': options.strip(), 'LOCAL_AI_VALIDATION_DIRECTORY': str(directory),
                              'LOCAL_AI_VALIDATION_RUN_ID': run_id, 'LOCAL_AI_VALIDATION_ROOT': str(workspace.root)}):
        result = workspace.command(argv, 120)
    check = {'name': name, **result, 'passed': False, 'tests': 0, 'executed': 0, 'tests_passed': 0,
             'validation_run_id': run_id, 'evidence': str(directory.relative_to(workspace.root))}
    try:
        paths = list(directory.glob('node-*.json'))
        if not paths or len(paths) > 1024:
            raise ValueError('لم يصدر مشغّل node:test تقرير تنفيذ لهذا الأمر. نجاح npm وحده ليس اختبارًا. استخدم node --test واختبارات حقيقية؛ لا تتجاوز مراسل الفحص بخيار reporter آخر. المشغّلات الأخرى تحتاج محوّل تقارير مدعومًا.')
        values = [receipt(path, run_id, 'node:test') for path in paths]
        counts = [test_counts(value) for value in values]
        ids = [t for value in values for t in value.get('successful_tests', [])]
        registered_names = {}
        for value in values:
            for name, count in value.get('registered_test_names', {}).items():
                registered_names[name] = registered_names.get(name, 0) + count
        identities = {key: item for value in values for key, item in value.get('test_identities', {}).items()}
        eligible = {t for t in ids if ids.count(t) == 1 and registered_names.get(t) == 1}
        # Keep old unique leaf IDs. Ambiguous leaves use their scoped identity.
        # Exact duplicates in the same file/suite remain ineligible.
        check['successful_tests'] = [t for t in dict.fromkeys(ids) if t in eligible and
                                    (t not in identities or identities[t]['legacy'] not in eligible)]
        check['test_identities'] = {t: identities[t] for t in check['successful_tests'] if t in identities}
        for scoped, item in identities.items():
            if item['legacy'] in check['successful_tests']:
                check['test_identities'][item['legacy']] = item
        totals = {key: sum(count[key] for count in counts) for key in counts[0]}
        check.update({k: v for k, v in totals.items() if k != 'passed'}, tests_passed=totals['passed'])
        check['passed'] = result['exit_code'] == 0 and confirmed(totals, all(v.get('success') is True for v in values))
        if not check['passed']:
            check['error'] = ('لم تُنفّذ اختبارات Node ناجحة فعلًا؛ صفر اختبارات أو جميعها skipped/todo لا تكفي.'
                              if totals['passed'] == 0 else 'فشلت اختبارات Node؛ راجع الفشل الفعلي في التقرير.')
    except (OSError, ValueError, KeyError, TypeError) as error:
        check['error'] = str(error)
    return check
