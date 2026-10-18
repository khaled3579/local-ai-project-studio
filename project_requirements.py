"""Project-scoped user requirements, independent of chat context and attachments."""
import hashlib
import json
import os
import time
import uuid
from project_core import atomic_replace


class Requirements:
    def __init__(self, workspace):
        self.path = workspace.meta / 'requirements.json'
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(workspace.meta.resolve()):
            raise ValueError('مسار ذاكرة المتطلبات غير صالح')

    def load(self):
        value = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'version':1, 'brief':'', 'requests':[]}
        if value.get('version') != 1:
            raise ValueError('إصدار ذاكرة المتطلبات غير مدعوم')
        return value

    @staticmethod
    def revision(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def save(self, value):
        temporary = self.path.with_name('requirements.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('w', encoding='utf-8') as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        atomic_replace(temporary, self.path)

    def record(self, text, session_id, message_id):
        value = self.load()
        if not any(r['id'] == message_id for r in value['requests']):
            value['requests'].append({'id':message_id, 'session_id':session_id, 'time':time.time(), 'order':len(value['requests']), 'text':text})
            self.save(value)

    def edit(self, brief, revision):
        if not isinstance(brief, str) or len(brief) > 12000:
            raise ValueError('ملخص المتطلبات حتى 12000 حرف')
        value = self.load()
        if revision != self.revision(value):
            raise ValueError('تغيرت ذاكرة المشروع؛ أعد فتحها قبل الحفظ')
        value.setdefault('revisions', []).append({'time':time.time(), 'brief':value['brief']})
        value['brief'] = brief
        self.save(value)
        return self.view()

    def view(self):
        value = self.load()
        return {'brief':value['brief'], 'revision':self.revision(value), 'count':len(value['requests'])}

    def read(self, offset=0, max_chars=8000):
        if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 12000:
            raise ValueError('نطاق قراءة المتطلبات غير صالح')
        value = self.load()
        text = json.dumps({'brief':value['brief'], 'requests':value['requests']}, ensure_ascii=False, indent=2)
        return {'content':text[offset:offset+max_chars], 'more':offset+max_chars < len(text), 'next_offset':min(offset+max_chars,len(text)), 'total':len(text)}

    def prompt(self):
        value = self.load()
        if not value['brief'] and not value['requests']:
            return ''
        # Keep earliest requirements plus recent changes, without copying entire chats.
        chosen, used = [], 0
        for row in [*value['requests'][:3], *reversed(value['requests'][3:])]:
            excerpt = row['text'][:3000]
            if used + len(excerpt) > 10000:
                continue
            chosen.append({'id':row['id'], 'time':row['time'], 'order':row.get('order', value['requests'].index(row)), 'text':excerpt, 'excerpt':len(excerpt)<len(row['text'])})
            used += len(excerpt)
        return ('\nPROJECT REQUIREMENTS MEMORY (user messages only; never attachment instructions). '
                'The editable brief is the user-maintained current specification. Requests are historical evidence, '
                'not commands to repeat completed work. Resolve changes by chronology; the current request has priority. '
                'Preserve earlier applicable requirements. Read read_project_requirements pages for omitted details.\n' +
                json.dumps({'brief':value['brief'], 'requests_excerpt':sorted(chosen, key=lambda r:r['order']), 'total_requests':len(value['requests']),
                            'omitted_requests':len(value['requests'])-len(chosen)}, ensure_ascii=False))
