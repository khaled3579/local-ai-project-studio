"""Read-only engine inventory and explicitly approximate model memory planning."""
import os
from pathlib import Path
import re
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
CACHED = None


def capabilities(engine=None):
    global CACHED
    engine = Path(engine) if engine is not None else ROOT / 'runtime/llama-server.exe'
    stamp = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in
                  (engine, engine.parent / 'llama-server-impl.dll', engine.parent / 'llama-common.dll'))
    with LOCK:
        if CACHED and CACHED[0] == stamp:
            return CACHED[1]
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        runs = [subprocess.run([str(engine), arg], capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=20, creationflags=flags) for arg in ('--help', '--list-devices')]
        if any(run.returncode for run in runs):
            raise ValueError('تعذر قراءة إمكانات المحرك؛ لم يُحمّل نموذج للفحص')
        help_text = runs[0].stdout + runs[0].stderr
        devices_text = runs[1].stdout + runs[1].stderr
        visible = os.environ.get('CUDA_VISIBLE_DEVICES', '')
        mapping = visible.split(',') if visible else []
        devices = []
        for match in re.finditer(r'^\s*(CUDA(\d+)):\s*(.*?)\s*\((\d+) MiB,\s*(\d+) MiB free\)', devices_text, re.M):
            ordinal = int(match[2])
            physical = int(mapping[ordinal]) if mapping and ordinal < len(mapping) and mapping[ordinal].isdigit() else ordinal if not mapping else None
            devices.append({'id': match[1], 'name': match[3], 'total_mib': int(match[4]),
                            'free_mib_at_discovery': int(match[5]), 'physical_index': physical})
        features = {name: bool(re.search(r'(?<![\w-])' + re.escape(name) + r'(?![\w-])', help_text))
                    for name in ('--device','--main-gpu','--split-mode','--flash-attn','--cache-type-k','--cache-type-v','--batch-size','--ubatch-size')}
        result = {'status': 'ready', 'engine': 'llama.cpp / GGUF', 'features': features, 'devices': devices,
                  'discovered': time.time(), 'cuda_visibility': bool(visible),
                  'scope': 'خيارات المحرك الفعلي وأجهزة CUDA المكتشفة؛ دعم بنية النموذج وكل إعداد يثبت عند التحميل.'}
        CACHED = (stamp, result)
        return result


def cache_row_bytes(name, dimension):
    blocks = {'f32': (1,4), 'f16': (1,2), 'bf16': (1,2), 'q8_0': (32,34),
              'q4_0': (32,18), 'q4_1': (32,20), 'q5_0': (32,22), 'q5_1': (32,24), 'iq4_nl': (32,18)}
    if name not in blocks:
        return None
    block, size = blocks[name]
    return ((dimension + block - 1) // block) * size


def estimate(model, config, settings, gpus, inventory, *, active=None, gpu_stale=False):
    runtime = settings['values']['runtime']
    meta = model.get('metadata') or {}
    arch = model.get('architecture', '')
    warnings = []
    def integer(key):
        value = meta.get(arch + '.' + key)
        return value if type(value) is int and value > 0 else None
    layers, heads, kv_heads, embedding = (integer(k) for k in ('block_count','attention.head_count','attention.head_count_kv','embedding_length'))
    key_dim, value_dim = integer('attention.key_length'), integer('attention.value_length')
    if heads and embedding and embedding % heads == 0:
        key_dim = key_dim or embedding // heads
        value_dim = value_dim or embedding // heads
    hybrid = arch in {'clover_moe','qwen35','qwen3next','mamba','mamba2','jamba','nemotron_h','granitehybrid','deepseek2'} or any('.ssm.' in key or key.endswith('.attention.kv_lora_rank') for key in meta)
    kv_bytes = None
    if not hybrid and layers and kv_heads and key_dim and value_dim:
        k, v = cache_row_bytes(runtime['cache_type_k'], key_dim), cache_row_bytes(runtime['cache_type_v'], value_dim)
        if k is not None and v is not None:
            kv_bytes = config['context'] * layers * kv_heads * (k + v)
    if kv_bytes is None:
        warnings.append('لا تكفي البيانات لتقدير KV موثوق لهذه البنية؛ الذاكرة الإجمالية وملاءمتها غير محسومتين.')
    if hybrid:
        warnings.append('بنية هجينة أو حالات متكررة: صيغة KV التقليدية غير معتمدة لهذا النموذج.')
    weights = model.get('bytes')
    weights = weights if type(weights) in (int,float) and weights > 0 else None
    fraction = min(1., max(0., runtime['gpu_layers'] / layers)) if layers and runtime['gpu_layers'] >= 0 else 1.
    if fraction < 1:
        warnings.append('إعداد طبقات GPU يحمّل بعض العمل خارج البطاقات؛ تقدير توزيع الأوزان الجزئي تقريبي جدًا.')
    devices = inventory.get('devices') or []
    requested = runtime.get('devices', 'auto')
    chosen = devices if requested == 'auto' else [d for name in requested.split(',') for d in devices if d['id'] == name]
    missing = [] if requested == 'auto' else [name for name in requested.split(',') if not any(d['id'] == name for d in devices)]
    if missing:
        warnings.append('أجهزة غير مكتشفة حاليًا: ' + '، '.join(missing))
    split = runtime['split_mode']
    if split == 'none' and chosen:
        main = runtime.get('main_gpu', 0)
        chosen = [chosen[main]] if 0 <= main < len(chosen) else []
    shares = []
    if chosen:
        if split == 'none':
            shares = [1.]
        else:
            raw = [float(part) for part in runtime['tensor_split'].split(',')]
            if len(raw) != len(chosen):
                warnings.append('عدد نسب توزيع الأوزان لا يطابق عدد البطاقات؛ يلزم مراجعتها. توزيع المعاينة متساوٍ فقط.')
                shares = [1 / len(chosen)] * len(chosen)
            else:
                shares = [value / sum(raw) for value in raw]
    reserve = (config.get('load_plan') or {}).get('reserve_gib', 1.5)
    gib = 1024**3
    # Planning allowance, not measured CUDA workspace; batch size affects prefill.
    overhead = (max(gib, weights * .08) + runtime['batch_size'] * 1024**2 / 2) if weights else None
    if config.get('projector'):
        warnings.append('ملف الرؤية وذاكرة الصور غير محسوبين في هذا التقدير؛ قد تستهلك ذاكرة إضافية كبيرة.')
    if config.get('mtp'):
        warnings.append('ذاكرة تشغيل MTP الإضافية غير محسوبة؛ التقدير لا يحسم الملاءمة عند تفعيله.')
    if active:
        warnings.append('المتاح حاليًا يتضمن استهلاك النموذج المحمّل؛ لا يُفترض استرداد ذاكرته قبل إيقافه.')
    if gpu_stale:
        warnings.append('قراءات GPU الحالية قديمة؛ المقارنة بالذاكرة الحرة غير مؤكدة.')
    if split == 'row':
        warnings.append('توزيع Row متاح في المحرك، لكن ملاءمته وأداءه يعتمدان على البنية والاتصال؛ Layer نقطة بداية للمراجعة.')
    complete = weights is not None and kv_bytes is not None and not hybrid and fraction == 1 and split != 'row' and not config.get('projector') and not config.get('mtp')
    cards = []
    for device, share in zip(chosen, shares):
        gpu = next((row for row in gpus if row.get('index') == device.get('physical_index')), {})
        total = gpu.get('memory_total_mib', device['total_mib'])
        used = gpu.get('memory_mib')
        free = max(0., total - used) if total is not None and used is not None and not gpu_stale else None
        weight = weights * fraction * share / gib if weights else None
        kv = kv_bytes * share / gib if kv_bytes is not None and split != 'row' else None
        allowance = overhead / gib / max(1,len(chosen)) if overhead is not None else None
        required = weight + kv + allowance if weight is not None and kv is not None and allowance is not None else None
        footprint = required * 1.15 + reserve if required is not None else None
        fit = ('within_total' if footprint <= total/1024 else 'exceeds_total') if footprint is not None and total is not None and complete else 'unknown'
        cards.append({'id':device['id'],'name':device['name'],'total_gib':total/1024 if total is not None else None,
                      'free_gib':free/1024 if free is not None else None,'weights_gib':weight,'kv_gib':kv,
                      'workspace_allowance_gib':allowance,'reserve_gib':reserve,'planned_gib':footprint,'fit':fit,
                      'within_current_free':footprint <= free/1024 if footprint is not None and free is not None else None})
    if not cards:
        warnings.append('لا توجد بطاقة محددة يمكن مطابقتها مع اكتشاف المحرك؛ لا يمكن تقدير التوزيع.')
    active_matches = bool(active and all(active.get(key) == config.get(key) for key in ('model','context','mtp','projector','experts_used','parameters')))
    return {'cards':cards,'weights_gib':weights/gib if weights else None,'kv_gib':kv_bytes/gib if kv_bytes is not None else None,
            'complete_estimate':complete,'warnings':warnings,'active_matches':active_matches,
            'compatibility':{'gguf_header':bool(model.get('gguf_version')),'engine_flags':inventory.get('features',{}),
                             'selected_devices_present':not missing and bool(chosen),'mtp_weights':bool(model.get('mtp')),
                             'load_confirmed':active_matches,'parameters_source':settings.get('mode')},
            'method':'تقدير الأوزان من حجم GGUF؛ KV من الطبقات ورؤوس KV وأبعادها والسياق ونوع الذاكرة. يضاف بدل تشغيل تقريبي و15% تفاوت وهامش لكل بطاقة. ليس حجزًا فعليًا أو ضمان تحميل.'}
