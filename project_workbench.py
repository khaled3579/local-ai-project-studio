"""Scoped, on-demand editor operations. No inference or validation on reads."""
import difflib

MAX_EDITOR_BYTES = 512 * 1024


def compare_text(before, after, path, source):
    if len(before) + len(after) > 200000 or max(before.count('\n'), after.count('\n')) > 4000:
        return {'summary_only':True, 'changed':before != after, 'message':'الملف كبير للمقارنة التفصيلية؛ لم يُقتطع محتوى المحرر أو يُحفظ أي تعديل.'}
    before, after = before.replace('\r\n','\n'), after.replace('\r\n','\n')
    left, right = before.splitlines(), after.splitlines()
    # splitlines misses a final-newline-only change; make it explicit.
    rows = list(difflib.unified_diff(left, right, fromfile=source+'/'+path, tofile='editor/'+path, lineterm=''))
    result = {'changed':before != after, 'diff':rows[:2500], 'truncated':len(rows)>2500,
              'added':sum(r.startswith('+') and not r.startswith('+++') for r in rows),
              'removed':sum(r.startswith('-') and not r.startswith('---') for r in rows),
              'final_newline_changed':before.endswith('\n') != after.endswith('\n')}
    return result


def validation_view(workspace, busy=False):
    report = workspace.last_validation()
    if not report:
        return {'status':'running' if busy else 'unchecked', 'checks':[], 'executed':0, 'passed':0, 'failed':0}
    status = ('running' if busy or report.get('in_progress') else 'outdated' if report.get('validation_outdated')
              else 'stale' if not report.get('current') else 'passed' if report['success'] else 'failed')
    checks = []
    for index, check in enumerate(report.get('checks', [])):
        # A successful build/syntax check is not an executed test.
        if report.get('in_progress') and check.get('name') == 'validation_incomplete':
            continue
        checks.append({'index':index, **check, 'executed':check.get('executed', 0),
                       'stdout':str(check.get('stdout') or '')[-20000:], 'stderr':str(check.get('stderr') or '')[-20000:]})
    return {'status':status, 'timestamp':report.get('timestamp'), 'fingerprint':report.get('fingerprint'),
            'checks':checks, 'executed':sum(c['executed'] for c in checks),
            'passed':sum(c.get('passed') is True for c in checks), 'failed':sum(c.get('passed') is not True for c in checks),
            'scope':report.get('scope'), 'validator_version':report.get('validator_version')}


def action(studio, value):
    with studio.lock:
        workspace = studio.workspace
        if (not workspace or value.get('project') != str(workspace.root) or value.get('session_id') != studio.session_id):
            raise ValueError('تغير المشروع أو المحادثة؛ أعد فتح الملف')
        operation = value.get('action')
        if operation == 'validation':
            return validation_view(workspace, studio.busy())
        if operation == 'preview':
            if studio.busy():
                raise ValueError('انتظر توقف المهمة قبل بدء المعاينة')
            path = workspace.path(value['path'])
            if path.suffix.lower() not in {'.html', '.htm'}:
                raise ValueError('المعاينة المضمنة لملفات HTML؛ استخدم تشغيل التطبيق لبقية الأنواع')
            if workspace.running and workspace.running.poll() is None:
                if getattr(workspace, 'preview_url', None):
                    return {'url':workspace.preview_url, 'existing':True}
                raise ValueError('تطبيق آخر يعمل؛ أوقفه أولًا')
            result = workspace.run(value['path'], [], True)
            return {'url':workspace.preview_url, 'result':result}
        if operation in {'versions', 'compare'} and studio.busy():
            raise ValueError('أوقف المهمة مؤقتًا قبل المقارنة مع سجل الإصدارات')
        path = workspace.path(value['path'])
        if path.stat().st_size > MAX_EDITOR_BYTES:
            raise ValueError('المحرر يدعم ملفات نصية حتى 512KB؛ استخدم محررًا خارجيًا للملفات الأكبر')
        current = workspace.read(value['path'])
        current['content'] = current['content'].replace('\r\n','\n')
        if '\x00' in current['content']:
            raise ValueError('الملف ثنائي؛ لا يُفتح كمصدر نصي')
        if operation == 'open':
            return {**current, 'bytes':path.stat().st_size}
        if operation == 'versions':
            versions = workspace.history.versions()
            return {'versions':[{k:v[k] for k in ('id','reason','timestamp')} for v in versions[:80]]}
        if operation == 'save':
            if studio.busy():
                raise ValueError('انتظر توقف المهمة قبل حفظ الملفات')
            if workspace.running and workspace.running.poll() is None:
                raise ValueError('أوقف المعاينة أو التطبيق قبل حفظ التعديل ثم أعد الفحص')
            content = value.get('content')
            if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_EDITOR_BYTES or '\x00' in content:
                raise ValueError('محتوى المحرر يجب أن يكون نصًا حتى 512KB')
            result = workspace.write(current['path'], content, value.get('sha256'))
            return {**result, **workspace.read(current['path'])}
        if operation == 'compare':
            after = value.get('content')
            if not isinstance(after, str) or len(after.encode('utf-8')) > MAX_EDITOR_BYTES:
                raise ValueError('محتوى المقارنة أكبر من الحد أو غير نصي')
            before, source = current['content'], 'disk'
            if value.get('version_id'):
                version = workspace.history.get(value['version_id'])
                item = next((f for f in version['files'] if f['path'] == current['path']), None)
                if item and item['bytes'] > MAX_EDITOR_BYTES:
                    raise ValueError('ملف الإصدار أكبر من حد المقارنة')
                before = workspace.history.blob(item).decode('utf-8-sig') if item else ''
                source = 'version-'+version['id'][:8]
            return {**compare_text(before, after, current['path'], source), 'path':current['path'], 'source':source,
                    'disk_changed':value.get('sha256') != current['sha256'], 'disk_sha256':current['sha256']}
        raise ValueError('عملية محرر غير صالحة')
