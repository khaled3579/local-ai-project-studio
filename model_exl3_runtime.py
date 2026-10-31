"""EXL3 placement metadata and load receipts; no generation or benchmarks."""
import hashlib
import json
import math
import os
from pathlib import Path
from model_tabby import RUNTIME, model_info


def prepare_stats(config):
    if not config.get('cpu_split_experts'):
        return
    import exllamav3.modules.block_sparse_mlp_cpu as cpu
    cache = RUNTIME / 'expert-stats'
    cache.mkdir(parents=True, exist_ok=True)
    cpu.EXPERT_STATS_DIR = str(cache)
    # Seed a private adaptive profile once; preserve the supplied model's file.
    info = model_info(config['model'])
    text = info['config'].get('text_config') or info['config']
    count = text['num_experts']
    key = hashlib.sha1(f"{os.path.abspath(config['model'])}|{count}".encode()).hexdigest()[:16]
    target = cache / (key + '.json')
    source = Path(config['model']) / 'expert_stats.json'
    if target.exists() or not source.is_file():
        return
    if source.stat().st_size > 16 * 1024**2:
        raise ValueError('ملف توزيع الخبراء أكبر من نطاق المراجعة')
    layers = json.loads(source.read_text(encoding='utf-8-sig'))
    if not isinstance(layers, dict) or not layers:
        raise ValueError('ملف إحصاءات الخبراء غير صالح')
    for name, values in layers.items():
        if not isinstance(name,str) or not isinstance(values,list) or len(values)!=count or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in values):
            raise ValueError('إحصاءات خبراء لا تطابق عدد خبراء النموذج')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'model_dir':config['model'],'layers':layers}),encoding='utf-8')
    temporary.replace(target)


def confirm(container, config):
    from exllamav3.modules.transformer import TransformerBlock
    devices, splits = {}, []
    for block in container.model.modules:
        if not isinstance(block, TransformerBlock):
            continue
        device = str(block.device)
        devices[device] = devices.get(device, 0) + 1
        mlp = block.mlp
        first = getattr(mlp, 'cpu_split_first', None)
        if first is not None:
            splits.append({'layer':block.key,'gpu_slots':first,'cpu_experts':mlp.num_experts-first})
    requested = config.get('cpu_split_experts', 0)
    if requested:
        text = model_info(config['model'])['config']
        text = text.get('text_config') or text
        if len(splits)!=text['num_hidden_layers'] or any(row['cpu_experts']!=requested for row in splits):
            raise RuntimeError('لم يؤكد المحرك تقسيم الخبراء المطلوب في جميع الطبقات')
    parallel = bool(config.get('tensor_parallel'))
    tp_devices = []
    if parallel:
        # TP shards live in worker models; parent block.device is staging
        # metadata, not evidence of layer placement on the participating GPUs.
        model = container.model
        tp_devices = list(getattr(model, 'active_devices', []))
        if not getattr(model, 'loaded_tp', False) or not tp_devices:
            raise RuntimeError('لم يؤكد المحرك اكتمال تحميل Tensor Parallel')
        children = getattr(model, 'mp_children', [])
        if any(type(d) is not int or d < 0 or d >= len(children) or
               children[d] is None or not children[d].is_alive() for d in tp_devices):
            raise RuntimeError('إحدى عمليات CUDA المشاركة في Tensor Parallel غير جاهزة')
        expected = len(config['gpu_split']) if config.get('gpu_split') else (
            len(config['devices'].split(',')) if config.get('devices') != 'auto' else len(tp_devices))
        if len(set(tp_devices)) != expected:
            raise RuntimeError('عدد بطاقات Tensor Parallel الفعلي لا يطابق المطلوب')
        devices = {}  # Do not mislabel parent staging devices as layer placement.
    elif config.get('gpu_split') and len(devices) < len(config['gpu_split']):
        raise RuntimeError('لم تُوزع طبقات النموذج على جميع البطاقات المطلوبة')
    mtp = bool(container.use_draft_model and container.draft_model is not None and container.draft_model.caps.get('mtp_draft') and container.draft_cache is not None and container.generator is not None)
    if config.get('mtp') and not mtp:
        raise RuntimeError('لم يؤكد المحرك تهيئة رأس MTP ومخزنه والمولد')
    return {'layer_devices':devices,'placement_mode':'tensor_parallel' if parallel else 'layer',
            'tensor_parallel_devices':tp_devices,'split_layers':len(splits),
            'cpu_experts_per_layer':requested,'gpu_expert_slots_per_layer':splits[0]['gpu_slots'] if splits else None,
            'gpu_budget_gib':config.get('gpu_split',[]),'mtp_loaded':mtp,
            'draft_tokens':container.draft_num_tokens if mtp else 0,
            'mtp_draft_ablated':False,'target_verifies_drafts':bool(config.get('mtp')),
            'pinned_cpu_arena':bool(getattr(container.config.infer_params,'moe_cpu_pinned_arena',False)),
            'performance_measured':False, 'acceptance_rate_measured':False}
