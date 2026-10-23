"""Per-request inference receipts, including incomplete and discarded generations."""
import json
import math
import time
from app_maintenance import RotatingLog, policy


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def stream_measurement(usage, timings, started, first_token, outcome):
    usage, timings = usage or {}, timings or {}
    details = usage.get('prompt_tokens_details')
    details = details if isinstance(details, dict) else {}
    return {'outcome': outcome, 'seconds': round(time.monotonic() - started, 6),
            'first_token_seconds': first_token,
            'prompt_tokens': _number(usage.get('prompt_tokens')),
            'completion_tokens': _number(usage.get('completion_tokens')),
            'cached_tokens': _number(details.get('cached_tokens')),
            'prompt_ms': _number(timings.get('prompt_ms')),
            'predicted_ms': _number(timings.get('predicted_ms')),
            'predicted_per_second': _number(timings.get('predicted_per_second'))}


def record_generation(job, workspace, measurement, request_id, *, step, protocol, estimated_input):
    """Saved totals survive resume in the existing job checkpoint. Unknown != zero."""
    totals = job.setdefault('token_usage', {'requests': 0, 'measured_requests': 0,
                                           'prompt_tokens': 0, 'completion_tokens': 0,
                                           'incomplete_requests': 0, 'seconds': 0.0})
    totals['requests'] += 1
    if measurement.get('prompt_tokens') is not None and measurement.get('completion_tokens') is not None:
        totals['measured_requests'] += 1
    for key in ('prompt_tokens', 'completion_tokens', 'seconds'):
        if measurement.get(key) is not None:
            totals[key] += measurement[key]
    if measurement['outcome'] != 'complete':
        totals['incomplete_requests'] += 1
    receipt = {**measurement, 'id': request_id, 'job_id': job['id'], 'step': step,
               'protocol': protocol, 'estimated_input_tokens': estimated_input, 'time': time.time()}
    job['last_inference'] = receipt
    target = workspace.meta / 'inference.jsonl'
    try:
        if not target.resolve().is_relative_to(workspace.meta.resolve()) or target.is_symlink():
            raise ValueError('Invalid inference receipt path')
        settings = policy(workspace.meta)
        RotatingLog(target, settings['log_mb'] * 1024 * 1024, settings['hot_archives']).write(
            json.dumps(receipt, ensure_ascii=False) + '\n')
    except (OSError, ValueError) as error:
        # Telemetry must never turn a completed mutation into a replayable error.
        job['metrics_warning'] = str(error)
    return receipt
