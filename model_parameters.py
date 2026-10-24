"""Validated, per-model inference settings and their declared provenance."""
import json
import math
from pathlib import Path


def field(label, default, flag, *, minimum=None, maximum=None, choices=None, integer=False):
    if integer and maximum is None:
        maximum = 2147483647  # Native engine's signed 32-bit argument representation.
    return dict(label=label, default=default, flag=flag, minimum=minimum, maximum=maximum,
                choices=choices, integer=integer)


GENERATION = {
    'temperature': field('الحرارة Temperature', .8, '--temp', minimum=0),
    'top_p': field('Top-P', .95, '--top-p', minimum=0, maximum=1),
    'top_k': field('Top-K — صفر لتعطيله', 40, '--top-k', minimum=0, integer=True),
    'min_p': field('Min-P', .05, '--min-p', minimum=0, maximum=1),
    'typical_p': field('Typical-P', 1., '--typical', minimum=0, maximum=1),
    'repeat_penalty': field('عقوبة التكرار — 1 لتعطيلها', 1., '--repeat-penalty', minimum=.000001),
    'repeat_last_n': field('نافذة التكرار — ‎-1 لكامل السياق', 64, '--repeat-last-n', minimum=-1, integer=True),
    'frequency_penalty': field('عقوبة التكرار العددي', 0., '--frequency-penalty'),
    'presence_penalty': field('عقوبة الظهور', 0., '--presence-penalty'),
    'mirostat': field('Mirostat', 0, '--mirostat', choices=[0, 1, 2], integer=True),
    'mirostat_tau': field('Mirostat Tau', 5., '--mirostat-ent', minimum=.000001),
    'mirostat_eta': field('Mirostat Eta', .1, '--mirostat-lr', minimum=.000001),
    'seed': field('البذرة Seed — ‎-1 لعشوائية جديدة', -1, '--seed', minimum=-1, maximum=4294967295, integer=True),
    'max_tokens': field('أقصى الإخراج — ‎-1 تلقائي حسب السياق', -1, '--n-predict', minimum=-1, integer=True),
    'thinking': field('التفكير — حسب دعم النموذج', 'auto', '--reasoning', choices=['auto', 'on', 'off']),
    'reasoning_effort': field('جهد التفكير — حسب دعم النموذج', 'default', '--reasoning-effort', choices=['default', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']),
    'reasoning_budget_tokens': field('ميزانية التفكير — ‎-1 افتراضي', -1, '--reasoning-budget', minimum=-1, integer=True),
}
RUNTIME = {
    'gpu_layers': field('طبقات GPU', 99, '--n-gpu-layers', minimum=-1, integer=True),
    'split_mode': field('توزيع العمل بين البطاقات', 'layer', '--split-mode', choices=['layer', 'row', 'none']),
    'tensor_split': field('نسب البطاقات — مثل 1,1', '1,1', '--tensor-split'),
    'flash_attention': field('Flash Attention', 'on', '--flash-attn', choices=['auto', 'on', 'off']),
    'cache_type_k': field('نوع ذاكرة K', 'q8_0', '--cache-type-k', choices=['f16', 'bf16', 'q8_0', 'q4_0', 'q4_1', 'q5_0', 'q5_1', 'iq4_nl']),
    'cache_type_v': field('نوع ذاكرة V', 'q8_0', '--cache-type-v', choices=['f16', 'bf16', 'q8_0', 'q4_0', 'q4_1', 'q5_0', 'q5_1', 'iq4_nl']),
    'batch_size': field('حجم الدفعة Batch', 512, '--batch-size', minimum=1, integer=True),
    'ubatch_size': field('حجم الدفعة الفعلية Micro-batch', 512, '--ubatch-size', minimum=1, integer=True),
    'threads': field('خيوط المعالج للتوليد', 8, '--threads', minimum=1, integer=True),
    'threads_batch': field('خيوط المعالج للمدخلات', 16, '--threads-batch', minimum=1, integer=True),
}
SCHEMA = {'generation': GENERATION, 'runtime': RUNTIME}
META = {'temperature': 'temp', 'top_p': 'top_p', 'top_k': 'top_k', 'min_p': 'min_p',
        'repeat_penalty': 'penalty_repeat', 'repeat_last_n': 'penalty_last_n',
        'mirostat': 'mirostat', 'mirostat_tau': 'mirostat_tau', 'mirostat_eta': 'mirostat_eta'}


def validate_group(group, value):
    if not isinstance(value, dict) or set(value) - set(SCHEMA[group]):
        raise ValueError('إعدادات غير معروفة: ' + group)
    result = {}
    for key, item in value.items():
        spec = SCHEMA[group][key]
        if spec['choices'] is not None:
            if item not in spec['choices'] or (spec['integer'] and type(item) is not int):
                raise ValueError('قيمة غير مدعومة: ' + spec['label'])
        elif key == 'tensor_split':
            if not isinstance(item, str):
                raise ValueError('نسب البطاقات يجب أن تكون نصًا مثل 1,1')
            try:
                parts = [float(p.strip()) for p in item.split(',')]
                valid = bool(parts) and all(math.isfinite(p) and p >= 0 for p in parts) and any(parts)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('نسب البطاقات أرقام غير سالبة ومجموعها أكبر من صفر')
        else:
            if type(item) not in (int, float) or not math.isfinite(item) or (spec['integer'] and type(item) is not int):
                raise ValueError('أدخل رقمًا صالحًا: ' + spec['label'])
            if spec['minimum'] is not None and item < spec['minimum'] or spec['maximum'] is not None and item > spec['maximum']:
                raise ValueError('القيمة خارج مجال المعامل: ' + spec['label'])
        result[key] = item
    return result


def normalize(value):
    if value is None:
        return {'mode': 'defaults'}
    if not isinstance(value, dict) or set(value) - {'mode', 'generation', 'runtime', 'stop'}:
        raise ValueError('صيغة إعدادات النموذج غير صالحة')
    mode = value.get('mode', 'defaults')
    if mode not in ('defaults', 'custom'):
        raise ValueError('وضع الإعدادات غير صالح')
    if mode == 'defaults':
        return {'mode': mode}
    stop = value.get('stop', [])
    if not isinstance(stop, list) or any(not isinstance(s, str) or not s for s in stop):
        raise ValueError('تسلسلات التوقف قائمة JSON من نصوص غير فارغة')
    return {'mode': mode, 'generation': validate_group('generation', value.get('generation', {})),
            'runtime': validate_group('runtime', value.get('runtime', {})), 'stop': stop}


def defaults(model):
    values = {group: {key: spec['default'] for key, spec in fields.items()} for group, fields in SCHEMA.items()}
    sources = {group: {key: 'افتراضي المحرك' if group == 'generation' else 'إعداد الجهاز الحالي' for key in fields} for group, fields in SCHEMA.items()}
    warnings = []
    for key, suffix in META.items():
        name = 'general.sampling.' + suffix
        candidate = model.get('metadata', {}).get(name)
        if candidate is not None:
            try:
                values['generation'].update(validate_group('generation', {key: candidate}))
                sources['generation'][key] = 'GGUF: ' + name
            except ValueError as error:
                warnings.append(str(error))
    # Only read declared adjacent settings; no network calls or name-based guesses.
    path = Path(model['path']).parent / 'generation_config.json'
    if path.is_file():
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('ملف generation_config.json كبير؛ لم تُقرأ منه افتراضيات')
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(config, dict):
                raise ValueError('generation_config.json ليس كائن JSON')
            for key in GENERATION:
                if key in config and config[key] is not None:
                    values['generation'].update(validate_group('generation', {key: config[key]}))
                    sources['generation'][key] = 'generation_config.json'
            if type(config.get('max_new_tokens')) is int:
                values['generation'].update(validate_group('generation', {'max_tokens': config['max_new_tokens']}))
                sources['generation']['max_tokens'] = 'generation_config.json: max_new_tokens'
            if config.get('do_sample') is False:
                values['generation']['temperature'] = 0.
                sources['generation']['temperature'] = 'generation_config.json: do_sample=false'
        except (OSError, ValueError) as error:
            warnings.append(str(error))
    return {'values': values, 'sources': sources, 'warnings': warnings}


def effective(model, config):
    result = defaults(model)
    profile = normalize(config.get('parameters'))
    if profile['mode'] == 'custom':
        for group in SCHEMA:
            result['values'][group].update(profile[group])
            result['sources'][group].update({key: 'تخصيص المستخدم' for key in profile[group]})
    result.update(mode=profile['mode'], stop=profile.get('stop', []))
    return result


def arguments(settings):
    argv = []
    for group, fields in SCHEMA.items():
        for key, spec in fields.items():
            value = settings['values'][group][key]
            argv.extend([spec['flag'], str(value)])
    for sequence in settings.get('stop', []):
        argv.extend(['--reverse-prompt', sequence])
    return argv


def apply_request(body, settings, *, allow_thought_suppression=False):
    """Keep the agent's protocol/schema while using the selected sampling policy."""
    generation = settings.get('values', {}).get('generation', {})
    for key, value in generation.items():
        if key in GENERATION and key not in ('thinking', 'reasoning_effort', 'max_tokens'):
            body[key] = 4294967295 if key == 'seed' and value == -1 else value
    thinking = generation.get('thinking', 'auto')
    if thinking != 'auto':
        body['chat_template_kwargs'] = {'enable_thinking': thinking == 'on'}
    effort = generation.get('reasoning_effort', 'default')
    if effort != 'default':
        body['reasoning_effort'] = effort
    if thinking == 'off':
        body['reasoning_budget_tokens'] = 0
    if settings.get('stop'):
        body['stop'] = list(settings['stop'])
    cap = generation.get('max_tokens', -1)
    if cap >= 0:
        body['max_tokens'] = min(body['max_tokens'], cap)
    if not allow_thought_suppression or thinking != 'off':
        body.pop('logit_bias', None)
    return body
