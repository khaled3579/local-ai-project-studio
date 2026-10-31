"""Strict OpenAI SSE assembly. Partial tool arguments are never executable."""
import json
import queue
import socket
import threading
import time
from project_metrics import stream_measurement


class GenerationInterrupted(Exception):
    pass


class GenerationStalled(ValueError):
    """A complete response cannot be trusted; discard every partial call."""
    pass


def repeated_suffix(text, *, max_width=80):
    tail=text[-max(2048,max_width*12):]
    for width in range(1,max_width+1):
        count=max(12,384//width)
        if len(tail)<width*count:continue
        unit=tail[-width:]
        if any(c.isalnum() for c in unit) and tail[-width*count:]==unit*count:
            return True
    return False


def abort_response(response):
    # shutdown wakes a blocked urllib3 reader; close alone can wait for its lock.
    try:
        response.raw._fp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):
        pass
    response.close()


def consume(response, interrupted, update, *, progress_timeout=45, total_timeout=240, measure=None, reasoning_update=None):
    inbox = queue.Queue(maxsize=64)
    stopped = threading.Event()

    def put(value):
        while not stopped.is_set():
            try:
                inbox.put(value, timeout=.1)
                return
            except queue.Full:
                pass

    def reader():
        try:
            for line in response.iter_lines(chunk_size=1):
                if stopped.is_set():
                    break
                if line:
                    put(line)
            put(None)
        except Exception as error:
            put(error)

    threading.Thread(target=reader, daemon=True).start()
    text, calls, finish, size = '', {}, None, 0
    started = getattr(response, 'inference_started', time.monotonic())
    usage, timings, first_token, outcome = {}, {}, None, 'incomplete'
    last_progress = started
    initial_timeout = getattr(response, 'initial_progress_timeout', progress_timeout)
    total_timeout = getattr(response, 'generation_total_timeout', total_timeout)
    progress_tail=''
    last_scan=0
    reasoning_tail=''
    reasoning_size=0
    reasoning_scan=0
    try:
        while True:
            if interrupted():
                raise GenerationInterrupted()
            now = time.monotonic()
            if now - started > total_timeout:
                raise GenerationStalled('بلغ التوليد ميزانية الوقت الكلية؛ أُلغي الرد الجزئي دون تنفيذ أدواته')
            if now - last_progress > (progress_timeout if first_token is not None else initial_timeout):
                raise GenerationStalled('توقف تقدم التوليد؛ أُلغي الرد الجزئي لتغيير خطة التوليد تلقائيًا')
            try:
                line = inbox.get(timeout=.1)
            except queue.Empty:
                continue
            if isinstance(line, Exception):
                raise line
            if line is None:
                raise ValueError('انقطع بث النموذج قبل اكتماله؛ لم تنفذ الأدوات الجزئية')
            if isinstance(line, bytes):
                line = line.decode('utf-8')
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                if repeated_suffix(reasoning_tail,max_width=256):
                    raise GenerationStalled('اكتُشف تكرار نصي داخل التفكير؛ أُلغي التوليد دون تنفيذ أدواته الجزئية')
                if finish not in {'stop', 'tool_calls', 'length'}:
                    raise ValueError('بث النموذج بلا علامة إكمال صالحة')
                message = {'role':'assistant', 'content':text}
                if calls:
                    ordered = [calls[k] for k in sorted(calls)]
                    if finish != 'length':
                        ids = set()
                        for call in ordered:
                            if not call['id'] or not call['function']['name'] or call['id'] in ids:
                                raise ValueError('استدعاء أداة غير مكتمل أو مكرر')
                            ids.add(call['id'])
                            if not isinstance(json.loads(call['function']['arguments']), dict):
                                raise ValueError('معاملات الأداة ليست كائنًا صالحًا')
                    message['tool_calls'] = ordered
                outcome = 'length' if finish == 'length' else 'complete'
                return {'message':message, 'finish_reason':finish}
            event = json.loads(data)
            if event.get('error'):
                raise RuntimeError(str(event['error']))
            if isinstance(event.get('usage'), dict):
                usage.update(event['usage'])
            if isinstance(event.get('timings'), dict):
                timings.update(event['timings'])
            choices = event.get('choices') or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get('delta') or {}
            reasoning = delta.get('reasoning_content') or delta.get('reasoning')
            if isinstance(reasoning, str) and reasoning:
                # Thinking is real generation progress, but never an executable action.
                last_progress = time.monotonic()
                if first_token is None:
                    first_token = round(last_progress - started, 6)
                if reasoning_update is not None:
                    reasoning_update(reasoning)
                # Reasoning is a separate stream: normal output may remain empty
                # throughout a thinking loop, bypassing the content/tool scanner.
                reasoning_tail=(reasoning_tail+reasoning)[-4096:]
                reasoning_size+=len(reasoning)
                if reasoning_size-reasoning_scan>=128:
                    reasoning_scan=reasoning_size
                    if repeated_suffix(reasoning_tail,max_width=256):
                        raise GenerationStalled('اكتُشف تكرار نصي داخل التفكير؛ أُلغي التوليد دون تنفيذ أدواته الجزئية')
            content = delta.get('content') or ''
            previous_size = size
            text += content
            progress_tail=(progress_tail+content)[-2048:]
            size += len(content)
            for fragment in delta.get('tool_calls') or []:
                index = fragment.get('index')
                if type(index) is not int or not 0 <= index < 64:
                    raise ValueError('فهرس أداة غير صالح')
                call = calls.setdefault(index, {'id':'', 'type':'function', 'function':{'name':'', 'arguments':''}})
                call['id'] += fragment.get('id') or ''
                for key in ('name', 'arguments'):
                    part = (fragment.get('function') or {}).get(key) or ''
                    call['function'][key] += part
                    progress_tail=(progress_tail+part)[-2048:]
                    size += len(part)
            if size > previous_size:
                last_progress = time.monotonic()
                if first_token is None:
                    first_token = round(last_progress - started, 6)
            if size-last_scan>=128:
                last_scan=size
                if repeated_suffix(progress_tail):
                    raise GenerationStalled('اكتُشف تكرار نصي داخل الرد؛ أُلغي قبل تنفيذ أي أداة جزئية')
            if size > 2 * 1024 * 1024:
                raise ValueError('تجاوز الرد حد الحجم الآمن')
            if choice.get('finish_reason'):
                finish = choice['finish_reason']
            update(text, [c['function']['name'] for c in calls.values()])
    finally:
        stopped.set()
        threading.Thread(target=abort_response, args=(response,), daemon=True).start()
        if measure is not None:
            # Observer errors cannot alter execution semantics of assembled tools.
            try:
                measure(stream_measurement(usage, timings, started, first_token, outcome))
            except Exception:
                pass
