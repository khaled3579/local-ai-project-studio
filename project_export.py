"""Create a portable source delivery with a current validation report."""
import hashlib
import io
import json
from pathlib import PurePosixPath
from urllib.parse import quote as url_quote
import time
import zipfile

EXCLUDE_DIRS = {'.local-ai', '.venv', 'venv', 'env', '.git', 'node_modules',
                '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
                '.tox', '.nox', '.cache', 'logs', 'tmp', 'temp', '__macosx'}
EXCLUDE_SUFFIXES = {'.pyc', '.pyo', '.log', '.tmp', '.temp', '.swp', '.swo', '.bak'}
MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 5000


def excluded(path):
    p = PurePosixPath(path)
    name = p.name.lower()
    return (any(part.lower() in EXCLUDE_DIRS for part in p.parts[:-1]) or
            p.suffix.lower() in EXCLUDE_SUFFIXES or name.endswith('~') or
            name in {'.ds_store', '.coverage', 'coverage.xml', '.env'} or
            (name.startswith('.env.') and name not in {'.env.example', '.env.sample', '.env.template'}))


def instructions(files, entry, report, docs_dir):
    names = {f['path'] for f in files}
    if entry not in names:
        entry = next((p for p in ['app.py', 'main.py', 'app.pyw', 'index.html', 'index.htm', 'index.js', 'server.js'] if p in names), '')
    lines = ['تعليمات تشغيل المشروع المصدّر', '',
             'فك ZIP في مجلد جديد وافتح PowerShell داخل جذر المشروع.',
             'ملفات البيئة والتشغيل المؤقتة غير مرفقة. أنشئ بيئة جديدة على الجهاز المستهدف.',
             'احتفظ بملفات README ومتطلبات المشروع؛ اتبعها لأي إعدادات إضافية.', '']
    quote = lambda s: "'" + s.replace("'", "''") + "'"
    if any(n.endswith(('.py', '.pyw')) for n in names):
        lines += ['يتطلب Python متوافقًا مع المشروع:', 'python -m venv .venv']
        if 'requirements.txt' in names:
            lines.append("& '.\\.venv\\Scripts\\python.exe' -m pip install -r requirements.txt")
        lines.append("& '.\\.venv\\Scripts\\python.exe' -m unittest discover -s " + ('tests' if any(n.startswith('tests/') for n in names) else '.') + " -p 'test*.py' -v")
        if entry.endswith(('.py', '.pyw')):
            lines += ['تشغيل نقطة الدخول المقترحة:', "& '.\\.venv\\Scripts\\python.exe' " + quote(entry)]
        else:
            lines.append('حدد نقطة الدخول من README؛ لم تُكتشف نقطة دخول Python تلقائيًا.')
    elif 'package.json' in names:
        lines += ['يتطلب Node.js وnpm بإصدار مناسب للمشروع.',
                  'npm ci' if 'package-lock.json' in names else 'npm install',
                  'شغّل scripts المذكورة في package.json (test وbuild وstart عند توفرها).']
        if entry.endswith(('.js', '.mjs')):
            lines += ['نقطة الدخول المقترحة:', 'node ' + quote(entry)]
    elif entry.endswith(('.html','.htm')) or any(n.endswith(('.html','.htm')) for n in names):
        if not entry.endswith(('.html','.htm')):entry=next(n for n in sorted(names) if n.endswith(('.html','.htm')))
        lines += ['معاينة مشروع الويب محليًا (يتطلب Python):', 'python -m http.server 8000 --bind 127.0.0.1',
                  'ثم افتح المتصفح على: http://127.0.0.1:8000/' + url_quote(entry, safe='/'),
                  'لتشغيل اختبارات JavaScript المثبتة في التقرير يلزم Node.js:', 'node --test']
    else:
        lines.append('راجع README لتثبيت الأدوات المطلوبة وتحديد أمر التشغيل.')
    lines += ['', 'حالة الفحص عند التصدير: ناجح ويطابق بصمة ملفات المشروع.',
              'عدد الاختبارات المسجلة: ' + str(sum(c.get('tests', 0) for c in report.get('checks', []))),
              'التقرير التفصيلي: ' + docs_dir + '/VALIDATION_REPORT.json',
              'بصمات الملفات وقائمة الاستبعاد: ' + docs_dir + '/MANIFEST.json',
              'التصدير لا يشغّل التطبيق مجددًا. نجاح الفحوص لا يثبت كل السلوكيات أو العرض الرسومي.',
              'أعد الفحص في البيئة الجديدة بعد تثبيت المتطلبات.']
    return '\n'.join(lines) + '\n'


def build_export(workspace, entry=''):
    report = workspace.last_validation()
    if not report or not report.get('success') or not report.get('current'):
        raise ValueError('افحص المشروع وأصلح أخطاءه قبل التصدير؛ يلزم تقرير ناجح يطابق الملفات الحالية')
    start = workspace.fingerprint(force=True)
    if start != report.get('fingerprint'):
        raise ValueError('تغيرت الملفات بعد الفحص؛ أعد فحص المشروع قبل التصدير')
    # Capture the exact per-file digests that produced the validated fingerprint.
    expected = {name:cached[1] for name,cached in workspace.hash_cache.items()}
    candidates = workspace.files()
    selected = [f for f in candidates if not excluded(f['path'])]
    if not selected:
        raise ValueError('لا توجد ملفات مشروع قابلة للتصدير')
    if len(selected) > MAX_FILES or sum(f['bytes'] for f in selected) > MAX_BYTES:
        raise ValueError('التصدير يدعم حتى 5000 ملف و200MB قبل الضغط')
    roots = {PurePosixPath(f['path']).parts[0].casefold() for f in candidates}
    docs = 'PROJECT_DELIVERY'
    index = 1
    while docs.casefold() in roots:
        docs = f'PROJECT_DELIVERY_{index}'
        index += 1
    manifest = {'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'project': workspace.root.name, 'source_fingerprint': start, 'files': [],
                'excluded_files': [f['path'] for f in candidates if excluded(f['path'])],
                'excluded_directory_rules': sorted(EXCLUDE_DIRS),
                'excluded_suffix_rules': sorted(EXCLUDE_SUFFIXES),
                'environment_files': '.env and .env.* except example/sample/template',
                'validation_scope': report.get('scope'), 'documentation_directory': docs}
    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in selected:
            path = workspace.path(item['path'])
            if path.is_symlink():
                raise ValueError('تغير مسار أحد الملفات أثناء التصدير؛ أعد الفحص')
            with path.open('rb') as source:
                raw = source.read(MAX_BYTES - total + 1)
            total += len(raw)
            if total > MAX_BYTES:
                raise ValueError('تجاوز حجم التصدير 200MB أثناء القراءة')
            if hashlib.sha256(raw).hexdigest() != expected.get(item['path']):
                raise ValueError('تغير محتوى ملف أثناء التصدير؛ لم يُعتمد ZIP، أعد الفحص')
            archive.writestr(item['path'], raw)
            manifest['files'].append({'path': item['path'], 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        end = workspace.fingerprint(force=True)
        if end != start:
            raise ValueError('تغيرت ملفات المشروع أثناء التصدير؛ أعد فحصه ثم صدّره مجددًا')
        archive.writestr(docs + '/VALIDATION_REPORT.json', json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr(docs + '/RUN_INSTRUCTIONS_AR.txt', instructions(selected, entry, report, docs))
        archive.writestr(docs + '/MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return buffer.getvalue(), manifest
