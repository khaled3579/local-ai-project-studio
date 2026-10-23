"""Persistent, content-addressed project snapshots and recoverable restoration."""
import difflib
import functools
import hashlib
import json
from pathlib import Path, PurePosixPath
import threading
import time
import uuid

MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 5000


def locked(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self.history.lock:
            return method(self, *args, **kwargs)
    return call


def recorded_command(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self.history.lock:
            self.history.checkpoint('قبل تنفيذ أمر')
            try:
                return method(self, *args, **kwargs)
            finally:
                self.history.checkpoint('بعد تنفيذ أمر')
    return call


class History:
    def __init__(self, workspace):
        self.ws = workspace
        self.lock = threading.RLock()

    @property
    def root(self):
        root = self.ws.meta / 'history'
        if not root.resolve().is_relative_to(self.ws.meta.resolve()):
            raise ValueError('مسار سجل الإصدارات خارج بيانات المشروع')
        root.mkdir(exist_ok=True)
        return root

    def save_json(self, path, value):
        from project_core import atomic_replace
        temp = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        atomic_replace(temp, path)

    def versions(self):
        with self.lock:
            versions = [json.loads(p.read_text(encoding='utf-8')) for p in self.root.glob('*.json')]
            return sorted(versions, key=lambda v: (v['timestamp'], v['id']), reverse=True)

    def get(self, version_id):
        if not isinstance(version_id, str) or len(version_id) != 32 or any(c not in '0123456789abcdef' for c in version_id):
            raise ValueError('معرّف الإصدار غير صالح')
        path = self.root / (version_id + '.json')
        if path.is_symlink():
            raise ValueError('مسار الإصدار غير صالح')
        return json.loads(path.read_text(encoding='utf-8'))

    def blob_path(self, digest):
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('بصمة ملف الإصدار غير صالحة')
        directory = self.root / 'objects'
        directory.mkdir(exist_ok=True)
        if not directory.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('مسار النسخ الاحتياطية غير صالح')
        path = directory / digest
        if path.is_symlink():
            raise ValueError('رابط في النسخ الاحتياطية غير مسموح')
        return path

    def blob(self, item):
        raw = self.blob_path(item['sha256']).read_bytes()
        if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('النسخة الاحتياطية تالفة؛ لم تبدأ استعادة الملفات')
        return raw

    def checkpoint(self, reason, report=None, force=False):
        from project_core import _retry_missing_file
        return _retry_missing_file(lambda: self._checkpoint_once(reason, report, force))

    def _checkpoint_once(self, reason, report=None, force=False):
        with self.lock:
            start = self.ws.fingerprint()
            report = self.ws.last_validation() if report is None else report
            if report and (report.get('fingerprint') != start or report.get('validation_outdated') or report.get('in_progress')):
                report = None
            validation = 'passed' if report and report.get('success') else 'failed' if report else 'unchecked'
            versions = self.versions()
            if not force and versions and versions[0]['fingerprint'] == start and versions[0]['validation'] == validation:
                for item in versions[0]['files']:
                    self.blob(item)  # Reused backups must still be intact before a mutation.
                return versions[0]
            candidates = self.ws.files()
            if len(candidates) > MAX_FILES or sum(f['bytes'] for f in candidates) > MAX_BYTES:
                raise ValueError('حفظ الإصدار يدعم حتى 5000 ملف و200MB؛ لم يبدأ التعديل')
            files, total = [], 0
            for item in candidates:
                with self.ws.path(item['path']).open('rb') as source:
                    raw = source.read(MAX_BYTES - total + 1)
                total += len(raw)
                if total > MAX_BYTES:
                    raise ValueError('تجاوزت نسخة المشروع 200MB؛ أُوقف حفظ الإصدار')
                digest = hashlib.sha256(raw).hexdigest()
                destination = self.blob_path(digest)
                if not destination.exists():
                    from project_core import atomic_replace
                    temp = destination.with_suffix('.' + uuid.uuid4().hex + '.tmp')
                    temp.write_bytes(raw)
                    atomic_replace(temp, destination)
                elif hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise ValueError('نسخة احتياطية تالفة؛ أُوقف التعديل لحماية السجل')
                files.append({'path': item['path'], 'bytes': len(raw), 'sha256': digest})
            captured = hashlib.sha256(json.dumps([(f['path'], f['sha256']) for f in files]).encode()).hexdigest()
            if self.ws.fingerprint() != start or captured != start:
                raise ValueError('تغير المشروع أثناء حفظ النسخة؛ أعد المحاولة')
            version = {'id': uuid.uuid4().hex, 'timestamp': time.time(), 'reason': str(reason)[:180],
                       'fingerprint': start, 'files': files, 'validation': validation, 'report': report}
            self.save_json(self.root / (version['id'] + '.json'), version)
            self.ws.event('version_saved', {'id': version['id'], 'message': version['reason']})
            return version

    def compare(self, version_id):
        with self.lock:
            version = self.get(version_id)
            current = self.checkpoint('نسخة الحالة الحالية للمقارنة')
            left = {f['path']: f for f in version['files']}
            right = {f['path']: f for f in current['files']}
            changes, remaining = [], 160000
            for path in sorted(set(left) | set(right)):
                a, b = left.get(path), right.get(path)
                if a and b and a['sha256'] == b['sha256']:
                    continue
                change = {'path': path, 'status': 'added' if not a else 'deleted' if not b else 'modified',
                          'before_bytes': a['bytes'] if a else 0, 'after_bytes': b['bytes'] if b else 0}
                if max(change['before_bytes'], change['after_bytes']) <= 512000 and remaining > 0:
                    try:
                        before = self.blob(a).decode('utf-8-sig') if a else ''
                        after = self.blob(b).decode('utf-8-sig') if b else ''
                        if '\x00' in before + after:
                            raise UnicodeError()
                        diff = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='version/' + path, tofile='current/' + path))
                        change['truncated'] = len(diff) > remaining
                        change['diff'] = diff[:remaining]
                        remaining -= len(change['diff'])
                    except UnicodeError:
                        change['binary'] = True
                else:
                    change['summary_only'] = True
                changes.append(change)
            return {'version': {k: version[k] for k in ('id', 'reason', 'timestamp', 'validation')},
                    'current_fingerprint': current['fingerprint'], 'changes': changes,
                    'direction': 'selected version -> current project', 'truncated': remaining <= 0}

    def restore(self, version_id, expected_fingerprint):
        with self.lock:
            if self.ws.running and self.ws.running.poll() is None:
                raise ValueError('أوقف تطبيق المشروع قبل استعادة إصدار')
            if expected_fingerprint != self.ws.fingerprint():
                raise ValueError('تغيرت الملفات منذ المقارنة؛ اعرض الفروق مجددًا قبل الاستعادة')
            target = self.get(version_id)
            captured = hashlib.sha256(json.dumps([(f['path'], f['sha256']) for f in target['files']]).encode()).hexdigest()
            if captured != target['fingerprint']:
                raise ValueError('فهرس الإصدار تالف؛ لم تبدأ الاستعادة')
            # Validate paths and every object before touching project files.
            names = set()
            for item in target['files']:
                name = item['path']
                path = self.ws.path(name)
                if path.relative_to(self.ws.root).as_posix() != name or '\\' in name or name.casefold() in names:
                    raise ValueError('مسار إصدار غير صالح أو متعارض')
                names.add(name.casefold())
                self.blob(item)
            backup = self.checkpoint('نسخة أمان قبل استعادة إصدار')
            if backup['fingerprint'] != expected_fingerprint:
                raise ValueError('تغير المشروع قبل الاستعادة؛ أعد المقارنة')
            try:
                self._apply(target)
                if self.ws.fingerprint() != target['fingerprint']:
                    raise ValueError('بصمة الاستعادة لا تطابق الإصدار')
            except Exception:
                self._apply(backup)
                raise
            # Keep earlier reports inside versions, and require fresh validation.
            validation_file = self.ws.meta / 'last_validation.json'
            if validation_file.exists():
                validation_file.unlink()
            restored = self.checkpoint('استعادة الإصدار ' + version_id[:8])
            self.ws.event('version_restored', {'id': version_id, 'backup_id': backup['id']})
            return {'restored': version_id, 'backup_id': backup['id'], 'version_id': restored['id'], 'requires_validation': True}

    def _apply(self, version):
        from project_core import atomic_replace
        desired = {f['path'] for f in version['files']}
        empty_candidates = set()
        for item in self.ws.files():
            if item['path'] not in desired:
                # This removal is recoverable through the pre-restore version.
                path = self.ws.path(item['path'])
                empty_candidates.update(p for p in path.parents if p != self.ws.root and p.is_relative_to(self.ws.root))
                path.unlink()
        for directory in sorted(empty_candidates, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()  # Only empty directories, never recursive deletion.
            except OSError:
                pass
        for item in version['files']:
            path = self.ws.path(item['path'])
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            temp.write_bytes(self.blob(item))
            atomic_replace(temp, path)
        self.ws.hash_cache.clear()
