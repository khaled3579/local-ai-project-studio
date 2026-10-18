"""Atomic, conversation-scoped composer text; uploaded attachments stay in sessions."""
import json
from pathlib import Path
import re
import threading
import time
import uuid
import os
from project_core import atomic_replace


class Drafts:
    def __init__(self, meta):
        self.root = Path(meta)
        self.path = self.root / 'composer-drafts.json'
        self.lock = threading.RLock()

    def read_all(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding='utf-8'))

    @staticmethod
    def empty():
        return {'text':'', 'revision':0, 'edit_id':None, 'consumed_edit_id':None, 'updated':None}

    def get(self, session):
        with self.lock:
            return self.read_all().get(session, self.empty())

    def write(self, path, value):
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('مسار المسودة غير صالح')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as f:
                json.dump(value, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            atomic_replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, session, text, edit_id, revision):
        if not isinstance(text, str) or len(text) > 200000:
            raise ValueError('المسودة نص حتى 200000 حرف')
        if not isinstance(edit_id, str) or not re.fullmatch(r'[a-f0-9-]{16,64}', edit_id):
            raise ValueError('معرّف تعديل المسودة غير صالح')
        if type(revision) is not int or revision < 0:
            raise ValueError('إصدار المسودة غير صالح')
        with self.lock:
            data = self.read_all()
            current = data.get(session, self.empty())
            if edit_id == current['consumed_edit_id']:
                return {'consumed':True, 'draft':current}
            if current['edit_id'] == edit_id:
                if current['text'] != text:
                    raise ValueError('لا يمكن إعادة استخدام معرّف التعديل لنص مختلف')
                return {'draft':current}
            if revision != current['revision']:
                # Both versions remain durable; the UI offers an explicit choice.
                directory = self.root / 'draft-conflicts'
                directory.mkdir(exist_ok=True)
                self.write(directory / (session + '-' + edit_id + '.json'),
                           {'current':current, 'incoming':{'text':text, 'edit_id':edit_id, 'revision':revision}})
                return {'conflict':True, 'draft':current}
            data[session] = {'text':text, 'edit_id':edit_id, 'revision':revision+1,
                             'updated':time.time(), 'consumed_edit_id':current['consumed_edit_id']}
            self.write(self.path, data)
            return {'draft':data[session]}

    def consume(self, session, edit_id):
        with self.lock:
            data = self.read_all()
            current = data.get(session, self.empty())
            if edit_id and current['edit_id'] == edit_id:
                data[session] = {**current, 'text':'', 'edit_id':None,
                                 'revision':current['revision']+1, 'updated':time.time(), 'consumed_edit_id':edit_id}
                self.write(self.path, data)
