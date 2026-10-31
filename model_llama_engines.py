"""Architecture-scoped optional llama.cpp engines. Original runtime is retained."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
CLOVER=ROOT/'runtime/engines/clover-4838ffb'
_VERIFIED=None

def select(model, *, verified=True):
    global _VERIFIED
    if model.get('architecture')!='clover_moe':return ROOT/'runtime/llama-server.exe'
    executable=CLOVER/'llama-server.exe';receipt=CLOVER/'installation.json'
    if not executable.is_file() or not receipt.is_file():
        raise ValueError('يتطلب Clover محرك clover_moe الاختياري؛ لم يُستبدل المحرك الأصلي')
    data=json.loads(receipt.read_text(encoding='utf-8'))
    if data.get('commit')!='4838ffb0c8ade92f34d9157deea38d92758ab824' or (verified and not data.get('api_verified')):
        raise ValueError('لم يثبت توافق محرك Clover بعد؛ لم يبدأ تحميله')
    rows=[]
    for name,expected in data.get('files',{}).items():
        path=(CLOVER/name).resolve()
        if path.parent!=CLOVER.resolve() or not path.is_file():
            raise ValueError('ملف محرك Clover مفقود أو خارج مجلده: '+name)
        stat=path.stat();rows.append((path,expected,stat.st_size,stat.st_mtime_ns))
    current=(str(CLOVER),receipt.stat().st_mtime_ns,tuple(rows))
    if _VERIFIED!=current:
        for path,expected,_,_ in rows:
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('تغير ملف محرك Clover؛ يلزم مراجعته: '+path.name)
        _VERIFIED=current
    if not data.get('files') or 'llama-server.exe' not in data['files']:
        raise ValueError('بصمات محرك Clover مفقودة')
    return executable

def identity(model):
    path=select(model)
    if path.parent==ROOT/'runtime':return None
    data=json.loads((path.parent/'installation.json').read_text(encoding='utf-8'))
    return {'engine':'llamacpp-clover','source_commit':data['commit'],
            'binary_sha256':data['files']['llama-server.exe'],
            'chat_template_sha256':data['files'].get('clover-chat-template.jinja'),'experimental':True}
