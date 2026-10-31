"""Durable checkpoints and a write-ahead tool execution ledger."""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager


class RecoveryReview(Exception):
    pass


class Recovery:
    def __init__(self, workspace):
        self.root = workspace.meta / 'recovery'
        self.root.mkdir(exist_ok=True)
        if not self.root.resolve().is_relative_to(workspace.meta.resolve()):
            raise ValueError('مسار سجل الاستعادة غير صالح')
        self.path = self.root / 'progress.sqlite3'
        if self.path.is_symlink():
            raise ValueError('ملف سجل الاستعادة غير صالح')
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoints(run_id TEXT PRIMARY KEY, updated REAL, payload TEXT, digest TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS calls(run_id TEXT, job_id TEXT, call_id TEXT, name TEXT, arguments TEXT, status TEXT, result TEXT, pid INTEGER, created REAL, PRIMARY KEY(run_id,job_id,call_id))')
            db.execute('CREATE TABLE IF NOT EXISTS execution_receipts(run_id TEXT, name TEXT, arguments TEXT, fingerprint TEXT, job_id TEXT, call_id TEXT, PRIMARY KEY(run_id,name,arguments,fingerprint))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _save(self, db, state):
        payload = json.dumps(state, ensure_ascii=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)', (state['run_id'], time.time(), payload, digest))

    def save(self, state):
        with self.connect() as db:
            self._save(db, state)

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM checkpoints WHERE run_id=?', (run_id,)).fetchone()
        if not row:
            return None
        return self._decode_checkpoint(row)

    @staticmethod
    def _decode_checkpoint(row):
        if hashlib.sha256(row['payload'].encode()).hexdigest() != row['digest']:
            raise ValueError('نقطة الاستعادة تالفة؛ لم تنفذ أي أوامر')
        return json.loads(row['payload'])

    def states(self):
        with self.connect() as db:
            rows = list(db.execute('SELECT * FROM checkpoints ORDER BY updated DESC'))
        # One SELECT reads a coherent snapshot; validate every payload exactly
        # as load() does. No missing or damaged checkpoint is silently skipped.
        return [self._decode_checkpoint(row) for row in rows]

    def get_call(self, run_id, job_id, call_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM calls WHERE run_id=? AND job_id=? AND call_id=?', (run_id, job_id, call_id)).fetchone()
        return dict(row) if row else None

    def begin(self, state, call_id, name, arguments):
        args = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        with self.connect() as db:
            row = db.execute('SELECT * FROM calls WHERE run_id=? AND job_id=? AND call_id=?', (state['run_id'], state['job']['id'], call_id)).fetchone()
            if row:
                if row['name'] != name or row['arguments'] != args:
                    raise RecoveryReview('تعارض معرّف أمر محفوظ؛ أُوقف التنفيذ للمراجعة')
                if row['status'] == 'started':
                    raise RecoveryReview('انقطع التنفيذ أثناء أمر ولم تُحفظ نتيجته؛ لن يُعاد تلقائيًا')
                return json.loads(row['result'])
            # A regenerated ID does not authorize replaying an operation whose
            # outcome is uncertain. Preserve the existing review gate.
            uncertain = db.execute("SELECT 1 FROM calls WHERE run_id=? AND name=? AND arguments=? AND status='started'",
                                   (state['run_id'], name, args)).fetchone()
            if uncertain:
                raise RecoveryReview('أمر مطابق ملتبس محفوظ؛ لن يعاد بمعرّف جديد')
            # Only successful source writes and completed commands have durable
            # receipts. Never reuse validation/browser/acceptance evidence or
            # process lifecycle operations from a different call.
            if name in {'write_file', 'patch_file', 'run_command'}:
                previous = db.execute('SELECT c.* FROM execution_receipts r JOIN calls c ON '
                    'c.run_id=r.run_id AND c.job_id=r.job_id AND c.call_id=r.call_id '
                    'WHERE r.run_id=? AND r.name=? AND r.arguments=? AND r.fingerprint=?',
                    (state['run_id'], name, args, state['fingerprint'])).fetchone()
                if previous and previous['status'] == 'completed':
                    value = json.loads(previous['result'])
                    value = {**value, 'reused_execution': True,
                             'original_call_id': previous['call_id'],
                             'note': 'Already executed on this unchanged source; not executed again.'}
                    self._save(db, state)
                    db.execute('INSERT INTO calls(run_id,job_id,call_id,name,arguments,status,result) VALUES(?,?,?,?,?,?,?)',
                        (state['run_id'], state['job']['id'], call_id, name, args, 'completed', json.dumps(value, ensure_ascii=False)))
                    return value
            self._save(db, state)
            db.execute('INSERT INTO calls(run_id,job_id,call_id,name,arguments,status) VALUES(?,?,?,?,?,?)',
                       (state['run_id'], state['job']['id'], call_id, name, args, 'started'))
        return None

    def process(self, run_id, job_id, call_id, pid):
        import psutil
        try:
            created = psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:
            pid, created = None, None
        with self.connect() as db:
            db.execute('UPDATE calls SET pid=?,created=? WHERE run_id=? AND job_id=? AND call_id=?', (pid, created, run_id, job_id, call_id))

    def finish(self, state, call_id, result, status='completed'):
        with self.connect() as db:
            db.execute('UPDATE calls SET status=?,result=? WHERE run_id=? AND job_id=? AND call_id=?',
                       (status, json.dumps(result, ensure_ascii=False), state['run_id'], state['job']['id'], call_id))
            row = db.execute('SELECT name,arguments FROM calls WHERE run_id=? AND job_id=? AND call_id=?',
                             (state['run_id'], state['job']['id'], call_id)).fetchone()
            if (row and status == 'completed' and not result.get('error') and not result.get('not_executed')
                    and not result.get('timed_out') and not result.get('waiting_for_user')
                    and ((row['name'] in {'write_file', 'patch_file'} and result.get('saved') and result.get('sha256'))
                         or (row['name'] == 'run_command' and result.get('exit_code') == 0))):
                db.execute('INSERT OR REPLACE INTO execution_receipts VALUES(?,?,?,?,?,?)',
                    (state['run_id'], row['name'], row['arguments'], state['fingerprint'], state['job']['id'], call_id))
            self._save(db, state)

    def calls(self, run_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM calls WHERE run_id=? ORDER BY rowid', (run_id,))]

    def uncertain(self, run_id):
        return [c for c in self.calls(run_id) if c['status'] == 'started']

    @staticmethod
    def alive(call):
        import psutil
        try:
            return call['pid'] and abs(psutil.Process(call['pid']).create_time() - call['created']) < .01 and psutil.Process(call['pid']).is_running()
        except (psutil.Error, TypeError):
            return False
