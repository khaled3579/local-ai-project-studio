"""Optional local TabbyAPI backend. Importing this module never starts a model."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import math

from model_parameters import validate_group

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'daily'
SAVED = DATA / 'tabby-settings.json'
SELECTION = DATA / 'engine-selection.json'
RUNTIME = ROOT / 'runtime/tabbyapi'
SOURCE = RUNTIME / 'source'
PYTHON = RUNTIME / '.venv/Scripts/python.exe'
COMMIT = '564f8fbb231a2d7e3b339574c85ae7d54ffd9618'
UPSTREAM_PORT = 18081
GENERATION_KEYS = {'temperature', 'top_p', 'top_k', 'min_p', 'repeat_penalty',
                   'repeat_last_n', 'frequency_penalty', 'presence_penalty', 'seed', 'max_tokens',
                   'thinking', 'reasoning_effort', 'reasoning_budget_tokens'}


def read_object(path):
    if path.stat().st_size > 4 * 1024**2:
        raise ValueError('ملف إعدادات كبير؛ لم يُستبدل: ' + path.name)
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('ملف الإعدادات ليس كائن JSON: ' + path.name)
    return value


def availability():
    receipt = RUNTIME / 'installation.json'
    ready = False
    error = None
    if receipt.is_file():
        try:
            ready = read_object(receipt).get('commit') == COMMIT
        except (ValueError, OSError) as exc:
            error = str(exc)
    ready = ready and PYTHON.is_file() and (SOURCE / 'main.py').is_file()
    return {'id': 'tabbyapi', 'name': 'TabbyAPI / ExLlamaV3', 'installed': ready,
            'status': 'installed_unverified' if ready else 'not_prepared', 'error': error,
            'runtime_verified': False, 'commit': COMMIT,
            'formats': ['EXL3 / supported safetensors'],
            'note': 'لا تُقبل GGUF هنا. التوافق والأداء غير مقاسين؛ التشغيل يتطلب أوزانًا وبنية يدعمهما ExLlamaV3.'}


def model_info(folder):
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError('أدخل مسار مجلد النموذج المحلي')
    path = Path(folder).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('اختر مسارًا محليًا مطلقًا لمجلد نموذج EXL3 أو safetensors؛ ليس ملف GGUF')
    path = path.resolve()
    try:
        config = read_object(path / 'config.json')
    except (OSError, ValueError) as error:
        raise ValueError('تعذّر قراءة config.json: ' + str(error)) from error
    if not any(path.glob('*.safetensors')):
        raise ValueError('مجلد النموذج لا يحتوي على ملفات safetensors؛ لم تُعدّل الملفات الأصلية')
    if not any((path / name).is_file() for name in ('tokenizer.json', 'tokenizer.model')):
        raise ValueError('ملف tokenizer.json أو tokenizer.model غير موجود')
    architecture = config.get('model_type') or str(config.get('architectures') or 'غير معلن')
    return {'path': str(path), 'name': path.name, 'architecture': architecture,
            'declared_context': (config.get('text_config') or config).get('max_position_embeddings'), 'config': config}


def validate(value, *, require_runtime=False):
    allowed = {'engine', 'model', 'context', 'cache_mode', 'tensor_parallel', 'devices', 'parameters', 'mtp', 'projector', 'cpu_offload_layers', 'heretic', 'cpu_split_experts', 'gpu_split', 'draft_tokens', 'chunk_size'}
    if not isinstance(value, dict) or set(value) - allowed or value.get('engine') != 'tabbyapi':
        raise ValueError('إعدادات محرك TabbyAPI غير صالحة')
    if value.get('projector'):
        raise ValueError('لا يُنقل ملف mmproj من llama.cpp إلى TabbyAPI')
    model = model_info(value.get('model', ''))
    # Older open pages omit newly introduced controls. Preserve the last
    # successful same-model values; an explicit zero/false remains intentional.
    inherited = {}
    if SAVED.is_file() and any(key not in value for key in ('cpu_offload_layers', 'heretic', 'cpu_split_experts', 'gpu_split', 'mtp', 'draft_tokens', 'chunk_size')):
        saved = read_object(SAVED)
        if str(saved.get('model','')).casefold() == model['path'].casefold():
            inherited = saved
    heretic = value.get('heretic', inherited.get('heretic', False))
    if type(heretic) is not bool:
        raise ValueError('خيار Heretic يجب أن يكون قيمة منطقية')
    if heretic:
        from model_heretic import descriptor
        descriptor(model['path'])
    context = value.get('context')
    if type(context) is not int or not 512 <= context <= 2147483392 or context % 256:
        raise ValueError('سياق TabbyAPI عدد صحيح من 512 ومضاعف 256')
    if type(model['declared_context']) is int and context > model['declared_context']:
        raise ValueError('السياق المطلوب يتجاوز السعة المعلنة في config.json')
    cache = value.get('cache_mode', 'FP16')
    if cache not in ('FP16', 'Q8', 'Q6', 'Q4'):
        raise ValueError('نوع ذاكرة TabbyAPI غير مدعوم')
    parallel = value.get('tensor_parallel', False)
    if type(parallel) is not bool:
        raise ValueError('Tensor parallel يجب أن يكون قيمة منطقية')
    if heretic and parallel:
        raise ValueError('ملحق Heretic المراجع يتطلب توزيع الطبقات؛ عطّل Tensor Parallel')
    offload = value.get('cpu_offload_layers', inherited.get('cpu_offload_layers', 0))
    text = model['config'].get('text_config') or model['config']
    layers = text.get('num_hidden_layers', 0)
    if type(offload) is not int or offload < 0 or (offload and (type(layers) is not int or offload > layers or not text.get('num_experts'))):
        raise ValueError('نقل الخبراء إلى RAM يتطلب نموذج MoE وعدد طبقات ضمن بنية النموذج')
    if offload and parallel:
        raise ValueError('نقل خبراء MoE إلى RAM يتطلب تعطيل Tensor Parallel واستخدام توزيع الطبقات')
    split = value.get('cpu_split_experts', inherited.get('cpu_split_experts', 0))
    experts = text.get('num_experts', 0)
    if type(split) is not int or split < 0 or (split and (type(experts) is not int or split >= experts)):
        raise ValueError('عدد خبراء CPU داخل الطبقة يجب أن يقل عن إجمالي خبراء النموذج')
    if split and (offload or parallel):
        raise ValueError('تقسيم الخبراء داخل الطبقة يتطلب صفر طبقات منقولة بالكامل وتعطيل Tensor Parallel')
    mtp = value.get('mtp', inherited.get('mtp', False))
    if type(mtp) is not bool:
        raise ValueError('خيار MTP يجب أن يكون قيمة منطقية')
    if mtp and (model['config'].get('architectures') != ['Qwen4ExpForConditionalGeneration'] or not text.get('mtp_num_hidden_layers') or not (Path(model['path'])/'mtp_hyper_connection_mixer_patch.safetensors').is_file()):
        raise ValueError('أوزان MTP أو بنية Qwen4Exp المراجعة غير متاحة لهذا النموذج')
    draft_tokens = value.get('draft_tokens', inherited.get('draft_tokens', 2))
    if type(draft_tokens) is not int or not 1 <= draft_tokens < context:
        raise ValueError('عدد توكنات MTP يجب أن يكون موجبًا وأصغر من السياق')
    chunk_size = value.get('chunk_size', inherited.get('chunk_size', 2048))
    if type(chunk_size) is not int or chunk_size < 256 or chunk_size % 256 or chunk_size > context:
        raise ValueError('دفعة الإدخال مضاعف 256 ولا تتجاوز السياق')
    gpu_split = value.get('gpu_split', inherited.get('gpu_split', []))
    if not isinstance(gpu_split, list) or any(type(v) not in (int,float) or not math.isfinite(v) or v <= 0 for v in gpu_split):
        raise ValueError('ميزانية البطاقات قائمة GiB موجبة أو [] للتلقائي')
    devices = value.get('devices', 'auto')
    if not isinstance(devices, str) or (devices != 'auto' and (
            not re.fullmatch(r'\d+(?:,\d+)*', devices) or len(set(devices.split(','))) != len(devices.split(',')))):
        raise ValueError('البطاقات: auto أو معرفات CUDA رقمية غير مكررة مثل 0,1')
    if gpu_split and devices != 'auto' and len(gpu_split) != len(devices.split(',')):
        raise ValueError('عدد ميزانيات الذاكرة لا يطابق البطاقات المختارة')
    parameters = value.get('parameters') or {'mode': 'defaults'}
    if not isinstance(parameters, dict) or set(parameters) - {'mode', 'generation', 'stop'} or parameters.get('mode') not in ('defaults', 'custom'):
        raise ValueError('إعدادات التوليد غير صالحة؛ إعدادات llama.cpp مستقلة')
    custom = parameters.get('generation') or {}
    if not isinstance(custom, dict) or set(custom) - GENERATION_KEYS:
        raise ValueError('إعداد توليد غير مدعوم في موصل TabbyAPI')
    custom = validate_group('generation', custom)
    if any(custom.get(key, 0) < 0 for key in ('frequency_penalty', 'presence_penalty')):
        raise ValueError('TabbyAPI يتطلب Frequency وPresence غير سالبين؛ لم يُتجاهل التخصيص')
    stops = parameters.get('stop', [])
    if not isinstance(stops, list) or len(stops) > 64 or any(not isinstance(s, str) or not s or len(s) > 4096 for s in stops):
        raise ValueError('تسلسلات التوقف يجب أن تكون قائمة نصوص غير فارغة')
    if require_runtime and not availability()['installed']:
        raise ValueError('TabbyAPI غير مهيأ. شغّل Prepare-Tabby.ps1 لتثبيت بيئته المعزولة؛ لم يتغير المحرك الحالي')
    if require_runtime:
        receipt = read_object(RUNTIME / 'installation.json')
        for name, expected in receipt.get('source_sha256', {}).items():
            path = (SOURCE / name).resolve()
            if not path.is_relative_to(SOURCE.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('تغير مصدر TabbyAPI بعد التهيئة؛ راجع ملف: ' + name)
        if not receipt.get('source_sha256'):
            raise ValueError('بصمات مصادر TabbyAPI مفقودة؛ لم يبدأ التحميل')
    return {'engine': 'tabbyapi', 'model': model['path'], 'context': context, 'mtp': mtp, 'projector': '',
            'cache_mode': cache, 'tensor_parallel': parallel, 'devices': devices, 'cpu_offload_layers': offload, 'heretic':heretic,
            'cpu_split_experts':split, 'gpu_split':gpu_split, 'draft_tokens':draft_tokens, 'chunk_size':chunk_size,
            'parameters': {'mode': parameters['mode'], 'generation': custom, 'stop': stops}}


def settings(config):
    """Omit undeclared sampling values so Tabby/model defaults remain authoritative."""
    values, sources, warnings = {}, {}, []
    path = Path(config['model']) / 'generation_config.json'
    if path.exists():
        declared = read_object(path)
        if declared.get('typical_p', 1) != 1:
            warnings.append('Typical-P المعلن غير مدعوم في ExLlamaV3؛ لم يُطبّق')
        for key in GENERATION_KEYS:
            candidate = declared.get('max_new_tokens' if key == 'max_tokens' else key)
            if candidate is not None:
                try:
                    if key in ('frequency_penalty', 'presence_penalty') and (type(candidate) not in (int, float) or candidate < 0):
                        raise ValueError('TabbyAPI لا يدعم قيمة سالبة لـ ' + key + ' في generation_config.json')
                    values.update(validate_group('generation', {key: candidate}))
                    sources[key] = 'generation_config.json'
                except ValueError as error:
                    warnings.append(str(error))
        if declared.get('do_sample') is False:
            values['temperature'], sources['temperature'] = 0., 'generation_config.json: do_sample=false'
    profile = config['parameters']
    if profile['mode'] == 'custom':
        values.update(profile['generation'])
        sources.update({key: 'تخصيص المستخدم' for key in profile['generation']})
    return {'mode': profile['mode'], 'values': {'generation': values, 'runtime': {}},
            'sources': {'generation': sources, 'runtime': {}}, 'stop': profile['stop'] if profile['mode'] == 'custom' else [],
            'warnings': warnings, 'engine': 'tabbyapi'}


def identity(config):
    rows = []
    for name in ('config.json', 'tokenizer_config.json', 'chat_template.jinja', 'generation_config.json',
                 'model.safetensors.index.json'):
        path = Path(config['model']) / name
        if path.is_file():
            rows.append([name, hashlib.sha256(path.read_bytes()).hexdigest()])
    for path in sorted(Path(config['model']).glob('*.safetensors')):
        stat = path.stat()
        rows.append([path.name, stat.st_size, stat.st_mtime_ns])
    receipt = RUNTIME / 'installation.json'
    if config.get('heretic'):
        from model_heretic import descriptor
        rows.append(['heretic', descriptor(config['model'])['sha256'], hashlib.sha256((ROOT/'model_heretic.py').read_bytes()).hexdigest()])
    return {'engine': 'tabbyapi-exllamav3', 'revision': COMMIT, 'model_files': rows,
            'runtime_receipt_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest() if receipt.is_file() else None}


def command(config):
    config = {**config, '_manager_pid':os.getpid()}
    directory = RUNTIME / 'launches'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json', dir=directory, delete=False) as stream:
        json.dump(config, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
        path = stream.name
    return [str(PYTHON), '-E', '-s', '-u', str(ROOT / 'model_tabby_worker.py'), path]


def adapt_request(body, *, configured_seed=None):
    body = dict(body)
    for old, new in {'repeat_penalty': 'repetition_penalty', 'repeat_last_n': 'repetition_range'}.items():
        if old in body:
            body[new] = body.pop(old)
    # Model IDs are supplied by the managed backend, never a stale llama.cpp ID.
    body.pop('model', None)
    for key in ('mirostat', 'mirostat_tau', 'mirostat_eta'):
        if key in body:
            raise ValueError('موصل TabbyAPI لا يقبل إعدادات Mirostat الخاصة بـ llama.cpp')
    if 'typical_p' in body or 'typical' in body:
        raise ValueError('Typical-P غير مدعوم في ExLlamaV3؛ لم يُتجاهل الإعداد')
    if body.get('reasoning_budget_tokens') == -1:
        body.pop('reasoning_budget_tokens')
    if body.get('seed') == 4294967295 and configured_seed != 4294967295:
        body.pop('seed', None)
    return body
