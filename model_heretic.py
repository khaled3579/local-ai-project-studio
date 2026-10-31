"""Reviewed runtime sidecar support. Never executes code from a model directory."""
import hashlib
import json
from pathlib import Path
import re


def descriptor(folder):
    root = Path(folder).resolve()
    path = root / 'abliteration.safetensors'
    if not path.is_file() or not path.resolve().is_relative_to(root) or path.stat().st_size > 16 * 1024**2:
        raise ValueError('ملف Heretic الجانبي مفقود أو غير صالح')
    config = json.loads((root / 'config.json').read_text(encoding='utf-8-sig'))
    if config.get('architectures') != ['Qwen4ExpForConditionalGeneration']:
        raise ValueError('دعم Heretic المراجع مخصص لبنية Qwen4Exp لهذا النموذج')
    return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'hidden_size':(config.get('text_config') or config).get('hidden_size'), 'path':str(path)}


def apply(model, folder):
    import torch
    from safetensors.torch import load_file
    from exllamav3.modules.transformer import TransformerBlock
    info = descriptor(folder)
    previous = getattr(model, '_studio_heretic_receipt', None)
    if previous:
        if previous['sidecar_sha256'] != info['sha256']:
            raise ValueError('تغير ملحق Heretic بعد تفعيله؛ يلزم إعادة تحميل النموذج')
        return previous
    tensors = load_file(info['path'], device='cpu')
    groups = {}
    for key, tensor in tensors.items():
        match = re.fullmatch(r'(.+)\.(abl_attn|abl_mlp)\.(a|b|s)', key)
        if not match or tuple(tensor.shape) != (info['hidden_size'],) or tensor.dtype != torch.float32:
            raise ValueError('متجه Heretic غير متوافق: ' + key)
        if not bool(torch.isfinite(tensor).all()):
            raise ValueError('متجه Heretic يحتوي قيمًا غير منتهية: ' + key)
        groups.setdefault((match[1],match[2]),{})[match[3]] = tensor
    blocks = {block.key:block for block in model.modules if isinstance(block, TransformerBlock)}
    plan = []
    for (key, kind), vectors in groups.items():
        if set(vectors) not in ({'a','b'}, {'a','b','s'}) or key not in blocks:
            raise ValueError('مجموعة متجهات Heretic غير مكتملة أو لا تطابق طبقات النموذج: ' + key)
        block = blocks[key]
        module = block.attn if kind == 'abl_attn' else block.mlp
        if module is None or hasattr(module, '_studio_heretic_original'):
            raise ValueError('طبقة Heretic مفقودة أو معدلة مسبقًا: ' + key)
        copied = {k:v.to(block.device, torch.float32).contiguous() for k,v in vectors.items()}
        plan.append((module, copied, key+'.'+kind))
    if not plan:
        raise ValueError('لم يُعثر على طبقات قابلة لتطبيق Heretic')

    def wrap(original, vectors):
        def forward(x, params, *args, **kwargs):
            y = original(x, params, *args, **kwargs)
            # Matches the reviewed sidecar's semantics: skip unchanged/prefill
            # outputs, modify each sublayer result before hyperconnection mixing.
            if y is x:
                return y
            yf = y if y.dtype == torch.float32 else y.float()
            projection = (yf * vectors['a']).sum(-1, keepdim=True)
            if 's' in vectors:
                yf.mul_(vectors['s'])
            yf.sub_(projection * vectors['b'])
            if yf is not y:
                y.copy_(yf)
            return y
        return forward

    changed = []
    try:
        for module, vectors, key in plan:
            original = module.forward
            module._studio_heretic_original = original
            changed.append(module)
            module.forward = wrap(original, vectors)
    except BaseException:
        for module in reversed(changed):
            module.forward = module._studio_heretic_original
            del module._studio_heretic_original
        raise
    receipt = {'enabled':True, 'applied':True, 'wrapped_sublayers':len(plan),
               'tensor_count':len(tensors), 'sidecar_sha256':info['sha256'],
               'application':'runtime_output_projection', 'quality_verified':False}
    model._studio_heretic_receipt = receipt
    return receipt


def install(main_module, config, state, runtime_state=None):
    """Run after normal loading, before API startup and any generation request."""
    runtime_state = runtime_state if runtime_state is not None else {}
    original_entry = main_module.entrypoint_async
    async def entry():
        from backends.exllamav3.model import ExllamaV3Container
        from model_exl3_runtime import prepare_stats, confirm
        prepare_stats(config)
        original_create = ExllamaV3Container.create_generator
        async def create(container, *args, **kwargs):
            if Path(container.model_dir).resolve() == Path(config['model']).resolve():
                # Install target hooks before the MTP generator binds the target
                # and before any graph capture. The draft head stays unmodified;
                # native speculative verification uses the modified target.
                if config.get('heretic'):
                    receipt = apply(container.model, config['model'])
                    state.update(receipt)
                    print('Heretic sidecar applied: ' + json.dumps(receipt), flush=True)
                await original_create(container, *args, **kwargs)
                runtime_state.update(confirm(container, config))
                print('EXL3 load configuration confirmed: ' + json.dumps(runtime_state), flush=True)
            else:
                await original_create(container, *args, **kwargs)
        ExllamaV3Container.create_generator = create
        try:
            return await original_entry()
        finally:
            ExllamaV3Container.create_generator = original_create
    main_module.entrypoint_async = entry
