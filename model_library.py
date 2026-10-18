"""Bounded GGUF header inspection. Never reads or maps tensor weights."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import threading
from project_model_info import expert_spec


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def inspect_gguf(path):
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as stream:
        def read(count):
            if count < 0 or stream.tell() + count > min(size, 256 * 1024**2):
                raise ValueError('GGUF header is truncated or exceeds 256 MiB')
            data = stream.read(count)
            if len(data) != count:
                raise ValueError('Truncated GGUF header')
            return data
        def number(fmt):
            return struct.unpack('<' + fmt, read(struct.calcsize(fmt)))[0]
        def string(keep=True):
            length = number('Q')
            if length > 16 * 1024**2:
                raise ValueError('GGUF string exceeds inspection limit')
            if keep:
                return read(length).decode('utf-8', errors='replace')
            read(length)
        formats = {0:'B', 1:'b', 2:'H', 3:'h', 4:'I', 5:'i', 6:'f', 7:'?', 10:'Q', 11:'q', 12:'d'}
        def value(kind, keep=True, depth=0):
            if depth > 2:
                raise ValueError('Nested GGUF array limit')
            if kind in formats:
                result = number(formats[kind])
                return result if not isinstance(result, float) or math.isfinite(result) else None
            if kind == 8:
                return string(keep)
            if kind != 9:
                raise ValueError('Unknown GGUF metadata type')
            subtype, count = number('I'), number('Q')
            if count > 2_000_000:
                raise ValueError('GGUF array exceeds inspection limit')
            if subtype in formats and count > 64:
                read(count * struct.calcsize(formats[subtype]))
                return {'items': count, 'type': subtype}
            values = [value(subtype, keep and count <= 64, depth + 1) for _ in range(count)]
            return values if keep and count <= 64 else {'items': count, 'type': subtype}
        if read(4) != b'GGUF':
            raise ValueError('Not a little-endian GGUF file')
        version = number('I')
        if version not in (2, 3):
            raise ValueError('Unsupported GGUF version')
        tensors, pairs = number('Q'), number('Q')
        if tensors > 200_000 or pairs > 100_000:
            raise ValueError('GGUF header entry limit')
        metadata = {}
        for _ in range(pairs):
            key = string()
            metadata[key] = value(number('I'))
        parameters, mtp_tensor = 0, False
        blocks = metadata.get(str(metadata.get('general.architecture', '')) + '.block_count', 0)
        blocks = blocks if type(blocks) is int else 0
        for _ in range(tensors):
            name = string()
            dimensions = number('I')
            if dimensions > 8:
                raise ValueError('Invalid tensor dimensions')
            shape = [number('Q') for _ in range(dimensions)]
            parameters += math.prod(shape)
            number('I'); number('Q')
            mtp_tensor |= name == f'blk.{blocks - 1}.nextn.eh_proj.weight'
    arch = metadata.get('general.architecture', '')
    if not isinstance(arch, str):
        raise ValueError('Invalid GGUF architecture')
    context = metadata.get(arch + '.context_length')
    return {'gguf_version': version, 'metadata': metadata, 'architecture': arch,
            'name': metadata.get('general.name') or path.stem,
            'declared_context': context if type(context) is int and context > 0 else None,
            'parameters_in_file': parameters, 'tensors': tensors,
            'mtp': mtp_tensor, 'projector': arch in ('clip', 'mmproj') or 'mmproj' in path.name.lower(),
            'adapter': metadata.get('general.type') == 'adapter'}


class ModelLibrary:
    def __init__(self, state_path, default_folder):
        self.path = Path(state_path)
        self.lock = threading.RLock()
        self.folder = str(default_folder)
        self.models, self.auxiliary, self.errors = [], [], []
        self.status, self.error = 'idle', None
        self.picking = False
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            self.folder = saved['folder']
        except (OSError, ValueError, KeyError):
            pass

    def scan(self, folder):
        folder = Path(folder).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError('مجلد النماذج غير موجود.')
        models, auxiliary, errors = [], [], []
        paths = []
        for directory, dirs, files in os.walk(folder, followlinks=False, onerror=lambda e: errors.append(str(e))):
            dirs[:] = [d for d in dirs if not Path(directory, d).is_symlink() and not Path(directory, d).is_junction()]
            paths.extend(Path(directory, f) for f in files if f.lower().endswith('.gguf'))
            if len(paths) > 10000:
                raise ValueError('المجلد يتجاوز 10,000 ملف GGUF؛ اختر مجلدًا أصغر.')
        for path in sorted(paths):
            split = re.match(r'^(.*)-(\d{5})-of-(\d{5})\.gguf$', path.name, re.I)
            if split and int(split[2]) != 1:
                continue
            try:
                info = inspect_gguf(path)
                if info['projector'] or info['adapter']:
                    auxiliary.append(str(path))
                    continue
                shards = [path]
                if split:
                    if int(split[3]) > 1024:
                        raise ValueError('عدد أجزاء النموذج يتجاوز حد الفهرسة')
                    shards = [path.with_name(f'{split[1]}-{i:05d}-of-{split[3]}.gguf') for i in range(1, int(split[3])+1)]
                    if len(shards) > 1024 or any(not p.is_file() for p in shards):
                        raise ValueError('أجزاء النموذج غير مكتملة')
                    for shard in shards[1:]:
                        part = inspect_gguf(shard)
                        info['parameters_in_file'] += part['parameters_in_file']
                        info['tensors'] += part['tensors']
                        info['mtp'] |= part['mtp']
                info.update(id=hashlib.sha256(str(path).casefold().encode()).hexdigest()[:24],
                            path=str(path), bytes=sum(p.stat().st_size for p in shards),
                            modified=path.stat().st_mtime, shards=len(shards),
                            load_status='لم يُختبر تحميله', projectors=[])
                info['expert_control']=expert_spec(info)
                models.append(info)
            except (OSError, ValueError, struct.error) as error:
                errors.append({'path': str(path), 'error': str(error)})
        for model in models:
            model['projectors'] = [p for p in auxiliary if Path(p).parent == Path(model['path']).parent and 'mmproj' in Path(p).name.lower()]
        with self.lock:
            atomic_json(self.path, {'folder': str(folder)})
            self.folder, self.models, self.auxiliary, self.errors = str(folder), models, auxiliary, errors
            self.status, self.error = 'ready', None

    def begin_scan(self, folder=None):
        with self.lock:
            if self.status == 'scanning':
                raise RuntimeError('فهرسة النماذج جارية.')
            self.status, self.error = 'scanning', None
        def work():
            try:
                self.scan(folder or self.folder)
            except Exception as error:
                with self.lock:
                    self.status, self.error = 'failed', str(error)
        threading.Thread(target=work, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return {'folder': self.folder, 'models': self.models, 'auxiliary_count': len(self.auxiliary),
                    'errors': self.errors, 'status': self.status, 'error': self.error, 'picking': self.picking}

    def resolve(self, model_id):
        with self.lock:
            for model in self.models:
                if model['id'] == model_id and Path(model['path']).is_file():
                    return model
        raise ValueError('النموذج غير موجود في الفهرس؛ أعد الفهرسة ثم اختره.')
