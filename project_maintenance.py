"""On-demand, scoped maintenance; never participates in the inference hot path."""
import json
from pathlib import Path
import sys
import time
from app_maintenance import policy,inventory,plan,archive,restore,contained,exclusive
from app_runtime import VERSION,doctor


def maintenance_action(studio,value):
    # Diagnostics can spawn bounded subprocesses. Never hold the controller lock
    # while waiting for them; streaming, pause and cancellation must stay responsive.
    if value.get('action') == 'doctor':
        with studio.lock:
            w=studio.workspace
            if not w or value.get('project')!=str(w.root) or value.get('session_id')!=studio.session_id:raise ValueError('تغير المشروع أو المحادثة؛ افتح الصيانة مجددًا')
        result=doctor(Path(__file__).resolve().parent,True)
        with studio.lock:
            if studio.workspace is not w or studio.session_id!=value['session_id']:raise ValueError('تغير المشروع أثناء التشخيص؛ افتح الصيانة مجددًا')
        return result
    with studio.lock:
        w=studio.workspace
        if not w or value.get('project')!=str(w.root) or value.get('session_id')!=studio.session_id:raise ValueError('تغير المشروع أو المحادثة؛ افتح الصيانة مجددًا')
        action=value.get('action','status')
        if action in {'policy','archive','restore','plan'} and (studio.busy() or (w.running and w.running.poll() is None)):raise ValueError('أوقف المهمة والتطبيق قبل تعديل سياسة الاحتفاظ أو الأرشفة')
        with exclusive(contained(w.meta,'.maintenance.lock')):
            if action=='policy':return {'policy':policy(w.meta,value.get('policy'))}
            if action=='plan':return plan(w.meta)
            if action=='archive':return archive(w.meta,value['id'])
            if action=='restore':return restore(w.meta,value['id'])
            if action!='status':raise ValueError('عملية صيانة غير معروفة')
            root=Path(__file__).resolve().parent
            archives=[]
            for p in contained(w.meta,'retention/archives').glob('*.json'):
                r=json.loads(p.read_text(encoding='utf-8'));archives.append({k:r[k] for k in ('id','created','bytes','status')})
            backups=[]
            for p in (root/'.updates/backups').glob('*/transaction.json'):
                r=json.loads(p.read_text(encoding='utf-8'));backups.append({k:r[k] for k in ('id','release','created')})
            return {'version':VERSION,'python':sys.executable,'schema':1,'policy':policy(w.meta),'storage':inventory(w.meta),
                    'system_logs':inventory(root/'daily/logs'),'archives':sorted(archives,key=lambda x:x['created'],reverse=True),'upgrade_backups':backups,
                    'protected':'المحادثات والمسودات والمرفقات وسجل الإصدارات ومحاولات الإصلاح ونقاط الاستعادة محفوظة بلا حذف تلقائي. تُنقل السجلات القديمة إلى أرشيف مضغوط، وتؤرشف ملفات التصدير القديمة بعد المعاينة فقط.'}
