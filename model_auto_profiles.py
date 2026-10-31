"""Per-engine/model successful profiles and conservative hardware defaults.

Planning never starts inference and never edits model weights or project data.
"""
import hashlib
import json
import os
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STORE=ROOT/'daily/automatic-model-profiles.json'

def key(engine, model):
    return engine+'|'+os.path.normcase(str(Path(model).resolve()))

def read_store():
    if not STORE.exists():return {'version':1,'models':{}}
    if STORE.stat().st_size>16*1024**2:raise ValueError('ملف إعدادات النماذج التلقائية كبير؛ لم يُستبدل')
    data=json.loads(STORE.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('models'),dict):
        raise ValueError('ملف الإعدادات التلقائية غير صالح؛ لم يُستبدل')
    for entry in data['models'].values():
        if not isinstance(entry,dict) or any(entry.get(k) is not None and not isinstance(entry[k],dict) for k in ('automatic','user')):
            raise ValueError('تخصيص نموذج غير صالح؛ لم يُستبدل الملف')
    return data

def engine_stamp(engine, model=None):
    from model_llama_engines import select
    executable = select(model or {}) if engine=='llamacpp' else None
    files=[ROOT/'runtime/tabbyapi/installation.json'] if engine=='tabbyapi' else [executable,executable.parent/'llama-server-impl.dll']
    return hashlib.sha256(json.dumps([(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in files if p.is_file()]).encode()).hexdigest()

def stamp(engine, model):
    path=Path(model['path'])
    if engine=='tabbyapi':
        files=[path/n for n in ('config.json','generation_config.json','model.safetensors.index.json','chat_template.jinja')]
        files+=list(path.glob('*.safetensors'))
    else:
        files=[path, path.parent/'generation_config.json']
        # Include every GGUF shard where the catalog provides the paths.
        files+= [Path(v) for v in model.get('shard_paths',[]) if isinstance(v,str)]
        split=re.match(r'^(.*)-\d{5}-of-\d{5}\.gguf$',path.name,re.I)
        if split:files+=list(path.parent.glob(split[1]+'-?????-of-?????.gguf'))
    items=[(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in files if p.is_file()]
    return hashlib.sha256(json.dumps(sorted(set(items))).encode()).hexdigest()

def hardware(inventory):
    rows=inventory.get('devices') or []
    return hashlib.sha256(json.dumps([(d['id'],d.get('name'),d.get('total_mib'),d.get('physical_index')) for d in rows],sort_keys=True).encode()).hexdigest()

def recommend(engine, model, inventory):
    """Approximate capacity policy; never label an estimate as a benchmark."""
    if inventory.get('status')!='ready':raise ValueError('انتظر اكتمال اكتشاف البطاقات قبل تجهيز الإعداد التلقائي')
    devices=[d for d in inventory.get('devices',[]) if d.get('physical_index') is not None]
    limit=model.get('declared_context')
    context=min(32768,limit) if type(limit) is int and limit>=512 else 8192
    context=max(512,context//256*256)
    notes=['توصية سعة محسوبة وليست إثباتًا لأسرع إعداد؛ التوافق النهائي يتأكد عند التحميل.']
    budgets=[max(.25,d['total_mib']/1024-2.5) for d in devices]
    if engine=='tabbyapi':
        cfg=model['config'];text=cfg.get('text_config') or cfg
        weights=sum(p.stat().st_size for p in Path(model['path']).glob('model*.safetensors'))/2**30
        # KV allowance is deliberately broad: hybrid recurrent state/vision is not exact.
        required=weights*1.12+2
        candidates=[i for i,b in enumerate(budgets) if b>=required]
        chosen=[devices[candidates[-1]]] if candidates else devices
        chosen_budgets=[budgets[candidates[-1]]] if candidates else budgets
        if text.get('num_experts') and weights>sum(chosen_budgets):
            notes.append('أوزان MoE تتجاوز الميزانية التقديرية؛ يلزم ضبط نقل الخبراء أو ملف مقاس متوافق، ولا تُفترض ملاءمة RAM تلقائيًا.')
        if not chosen:raise ValueError('لا توجد بطاقة CUDA مكتشفة لـ EXL3؛ لم يُنشأ إعداد GPU وهمي')
        result={'engine':engine,'model':model['path'],'context':context,'mtp':False,'projector':'',
                'cache_mode':'Q8','tensor_parallel':False,'devices':','.join(str(d['physical_index']) for d in chosen),
                'cpu_offload_layers':0,'cpu_split_experts':0,'heretic':False,'gpu_split':[round(v,2) for v in chosen_budgets],
                'draft_tokens':2,'chunk_size':min(2048,context),'parameters':{'mode':'defaults','generation':{},'stop':[]}}
        notes.append('لا يُفعّل MTP أو Heretic أو Tensor Parallel دون ملف تحميل/قياس متوافق. ميزانيات البطاقات GiB وليست نسبًا.')
    else:
        weights=(model.get('bytes') or 0)/2**30
        meta=model.get('metadata') or {};arch=model.get('architecture','')
        layers=meta.get(arch+'.block_count',0);kvheads=meta.get(arch+'.attention.head_count_kv',0)
        heads=meta.get(arch+'.attention.head_count',0);embedding=meta.get(arch+'.embedding_length',0)
        hybrid=arch in {'clover_moe','qwen35','qwen3next','mamba','mamba2','jamba','nemotron_h','granitehybrid','deepseek2'}
        dim=embedding//heads if heads and embedding else 0
        known=bool(layers and kvheads and dim and not hybrid)
        kv=context*layers*kvheads*dim*4/2**30 if known else 2
        required=weights*1.15+kv+1.5
        candidates=[i for i,b in enumerate(budgets) if known and b>=required]
        chosen=[devices[candidates[-1]]] if candidates else devices
        total=sum(budgets[devices.index(d)] for d in chosen)
        fraction=min(1.,max(0.,(total-kv-1.5)/max(.01,weights*1.15))) if chosen else 0
        gpu_layers=int(layers*fraction) if layers else (99 if chosen else 0)
        if not known:notes.append('بنية هجينة أو بيانات KV ناقصة؛ الذاكرة تقديرية وتوزيع البطاقة الواحدة غير مفترض.')
        if fraction<1:notes.append('قد تُحمّل بعض الطبقات في RAM؛ لم تُخفّض دقة الأوزان أو تُغيّر ملفات النموذج.')
        features=inventory.get('features') or {}
        runtime={'gpu_layers':gpu_layers,'batch_size':512,'ubatch_size':256}
        if features.get('--device') and chosen:runtime['devices']=','.join(d['id'] for d in chosen)
        if features.get('--split-mode'):runtime['split_mode']='none' if len(chosen)<=1 else 'layer'
        if chosen:runtime.update(main_gpu=0,tensor_split=','.join(str(round(budgets[devices.index(d)],2)) for d in chosen))
        if features.get('--flash-attn'):runtime['flash_attention']='on'
        # FP16 is the conservative KV default; don't assume every architecture supports quantized V.
        if features.get('--cache-type-k'):runtime['cache_type_k']='f16'
        if features.get('--cache-type-v'):runtime['cache_type_v']='f16'
        result={'model':model['path'],'context':context,'mtp':False,'projector':'',
                'parameters':{'mode':'custom','generation':{},'runtime':runtime,'stop':[]},
                'load_plan':{'profile':'balanced','reserve_gib':2.5}}
    return result,notes

def profile(engine, model, inventory, *, legacy=None):
    data=read_store();entry=data['models'].get(key(engine,model['path']),{})
    defaults,notes=recommend(engine,model,inventory)
    same=entry.get('model_stamp')==stamp(engine,model) and entry.get('hardware_stamp')==hardware(inventory) and entry.get('engine_stamp')==engine_stamp(engine,model)
    basis='estimated'
    if same and entry.get('automatic'):
        defaults=entry['automatic'];basis=entry.get('basis','load_confirmed')
        notes.append('افتراضيات محفوظة لنفس النموذج والبطاقات؛ نجاح التحميل لا يثبت أفضل سرعة.')
        if basis=='measured':notes[0]='إعداد ثبت بقياس سابق مطابق للنموذج والبطاقات؛ لا يمثل ضمانًا لكل المهام.'
    saved=entry.get('user') or legacy
    if saved and (saved.get('model')!=model['path'] or saved.get('engine','llamacpp')!=engine):saved=None
    if entry and not same:notes.append('تغيّرت هوية النموذج أو البطاقات؛ أُعيد حساب الافتراضيات، والتخصيص السابق متاح للمراجعة.')
    return {'engine':engine,'model':model['path'],'defaults':defaults,'saved':saved,
            'recommended':saved or defaults,'basis':basis,'warnings':notes,'has_saved':bool(saved)}

def updated_store(engine, model, inventory, config, legacy=None):
    data=read_store()
    if inventory.get('status') != 'ready':
        data['models'][key(engine,model['path'])]={'model_stamp':stamp(engine,model),'hardware_stamp':hardware(inventory),'engine_stamp':engine_stamp(engine,model),
            'automatic':None,'user':config,'basis':'unavailable'}
        return data
    view=profile(engine,model,inventory,legacy=legacy)
    data['models'][key(engine,model['path'])]={'model_stamp':stamp(engine,model),'hardware_stamp':hardware(inventory),'engine_stamp':engine_stamp(engine,model),
        'automatic':view['defaults'],'user':config,'basis':view['basis']}
    return data
