"""Owned TabbyAPI process plus loopback compatibility gateway; no inference retries.

Tabby executes in this process, so terminating the owned worker also frees its
CUDA allocations. It never attaches to or terminates a pre-existing server.
"""
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import importlib
import secrets
import select
import socket
import shutil
import sys
import threading
import time

import requests
from model_tabby import SOURCE, RUNTIME, UPSTREAM_PORT, validate, adapt_request, settings


class RedactedStream(io.TextIOBase):
    def __init__(self, stream, keys):
        self.stream, self.keys = stream, keys

    def write(self, value):
        for key in self.keys:
            value = value.replace(key, '[local credential]')
        return self.stream.write(value)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return False

    @property
    def encoding(self):
        return self.stream.encoding

    def fileno(self):
        return self.stream.fileno()


def watch_parent(manager_pid):
    """Hold an actual parent handle: PID reuse cannot keep an orphan alive."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    # Windows venv launchers can be an intermediate parent. Watch both the
    # launcher and manager, so a stop or a manager crash cannot orphan CUDA work.
    for pid in {manager_pid, os.getppid()}:
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), 'Cannot monitor the model owner')
        def monitor(owner=handle):
            if kernel.WaitForSingleObject(owner, 0xFFFFFFFF) == 0:
                os._exit(1)
        threading.Thread(target=monitor, daemon=True).start()


def gateway(config, key, control_token, heretic_state, runtime_state):
    lock = threading.Lock()
    state = {'active': 0, 'quiesced': False}
    upstream = f'http://127.0.0.1:{UPSTREAM_PORT}'
    sampling = settings(config)['values']['generation']
    origins = {f'http://{host}:{port}' for host in ('localhost', '127.0.0.1') for port in (18078, 18079, 18080)}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, format, *args):
            pass

        def send(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionError):
                self.close_connection = True

        def allowed(self):
            # Block cross-origin web pages and DNS rebinding, including /slots.
            if self.headers.get('Host') not in ('127.0.0.1:18080', 'localhost:18080') or self.headers.get('Origin') not in origins | {None}:
                self.send({'error': 'origin rejected'}, 403)
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            if self.path == '/slots':
                with lock:
                    active = state['active']
                self.send([{'is_processing': active > 0}])
            elif self.path in ('/health', '/props'):
                self.forward('GET', self.path)
            elif self.path == '/':
                self.send({'message': 'استخدم محادثة مساحة المشاريع؛ واجهة llama.cpp لا تخص TabbyAPI', 'studio': 'http://127.0.0.1:18078/'})
            else:
                self.send({'error': 'not found'}, 404)

        def do_POST(self):
            if not self.allowed():
                return
            active = False
            try:
                size = int(self.headers.get('Content-Length', '0'))
                # Leave room for the existing attachment allowance after base64
                # encoding, chat history and tool schemas; do not truncate input.
                if not 0 <= size <= 512 * 1024**2:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
                if self.path == '/studio/quiesce':
                    if not secrets.compare_digest(self.headers.get('X-Engine-Control', ''), control_token):
                        self.send({'error': 'request rejected'}, 403)
                        return
                    with lock:
                        if state['active']:
                            self.send({'error': 'انتظر اكتمال الرد قبل تبديل المحرك'}, 409)
                            return
                        state['quiesced'] = True
                    self.send({'quiesced': True})
                    return
                if self.path == '/tokenize':
                    self.forward('POST', '/v1/token/encode', {'text': body['content'],
                        'add_bos_token': bool(body.get('add_special', False)),
                        'encode_special_tokens': bool(body.get('parse_special', True))})
                elif self.path == '/apply-template':
                    if self.headers.get('X-Studio-Accounting') == '1':
                        body.pop('reasoning_effort', None)
                        body.pop('chat_template_kwargs', None)
                        if sampling.get('thinking', 'auto') != 'auto':
                            body['chat_template_kwargs'] = {'enable_thinking':sampling['thinking'] == 'on'}
                        if sampling.get('reasoning_effort', 'default') != 'default':
                            body['reasoning_effort'] = sampling['reasoning_effort']
                    self.forward('POST', '/apply-template', adapt_request(body, configured_seed=sampling.get('seed')))
                elif self.path == '/v1/chat/completions':
                    body = adapt_request(body, configured_seed=sampling.get('seed'))
                    with lock:
                        if state['quiesced']:
                            self.send({'error': 'المحرك قيد الإيقاف؛ الطلب لم يُرسل ولم يُعد تشغيله'}, 409)
                            return
                        state['active'] += 1
                        active = True
                    self.forward('POST', self.path, body)
                else:
                    self.send({'error': 'not found'}, 404)
            except (ValueError, KeyError) as error:
                self.send({'error': str(error)}, 400)
            finally:
                if active:
                    with lock:
                        state['active'] -= 1

        def forward(self, method, path, body=None):
            session = requests.Session()
            session.trust_env = False  # Local prompts never pass through an OS HTTP proxy.
            response = None
            sent = False
            finished = threading.Event()
            disconnected = threading.Event()
            watcher = None

            def watch_disconnect():
                # A client can cancel while EXL3 is still prefilling, before
                # any output reaches wfile. Wake the upstream reader promptly
                # so Tabby sees the disconnect and releases the old generation.
                while not finished.wait(.25):
                    try:
                        readable, _, _ = select.select([self.connection], [], [], 0)
                        if not readable or self.connection.recv(1, socket.MSG_PEEK):
                            continue
                    except OSError:
                        pass
                    disconnected.set()
                    if response is not None:
                        try:
                            response.raw._fp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                        except (AttributeError, OSError):
                            pass
                    return

            try:
                response = session.request(method, upstream + path, json=body,
                    headers={'Authorization': 'Bearer ' + key}, stream=True,
                    timeout=(5, max(300, min(1830, 90 + config['context'] / 16))
                             if config.get('cpu_split_experts') or config.get('cpu_offload_layers') else 300),
                    allow_redirects=False)
                content_type = response.headers.get('Content-Type', 'application/json')
                if method == 'GET' and path == '/health' and response.status_code == 200:
                    value = response.json()
                    self.send({'status': 'ok' if value.get('status') == 'healthy' else 'failed', 'upstream': value},
                              200 if value.get('status') == 'healthy' else 503)
                    return
                if method == 'GET' and path == '/props' and response.status_code == 200:
                    value = response.json()
                    value['studio_heretic'] = dict(heretic_state)
                    value['studio_runtime'] = dict(runtime_state)
                    self.send(value)
                    return
                self.send_response(response.status_code)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                sent = True
                self.close_connection = True
                if 'text/event-stream' in content_type:
                    watcher = threading.Thread(target=watch_disconnect, daemon=True)
                    watcher.start()
                # No retry: a tool call might already have been generated.
                for block in response.iter_content(chunk_size=None):
                    if disconnected.is_set():
                        break
                    if block:
                        self.wfile.write(block)
                        if 'text/event-stream' in content_type:
                            self.wfile.flush()
            except requests.RequestException:
                if not sent:
                    self.send({'error': 'تعذر الاتصال بمحرك TabbyAPI؛ لم يُعد إرسال الطلب تلقائيًا'}, 502)
                else:
                    self.close_connection = True
            except (BrokenPipeError, ConnectionError):
                self.close_connection = True
            finally:
                finished.set()
                if watcher is not None:
                    watcher.join(timeout=1)
                if response is not None:
                    response.close()
                session.close()

    server = ThreadingHTTPServer(('127.0.0.1', 18080), Handler)
    # A failed model load must exit even while callers are polling /health.
    # Do not retain CUDA allocations waiting for disconnected request threads.
    server.daemon_threads = True
    server.block_on_close = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    launch_path = Path(sys.argv[1]).resolve()
    if not launch_path.is_relative_to((RUNTIME / 'launches').resolve()):
        raise ValueError('Invalid owned launch configuration')
    raw = json.loads(launch_path.read_text(encoding='utf-8'))
    launch_path.unlink()
    control = raw.pop('_control_token')
    manager_pid = raw.pop('_manager_pid')
    config = validate(raw, require_runtime=True)
    watch_parent(manager_pid)
    # Dedicated cwd: never overwrite a user's Tabby config/auth or original model.
    run_dir = RUNTIME / 'sessions' / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4))
    run_dir.mkdir(parents=True)
    for name in ('templates', 'sampler_overrides'):
        if (SOURCE / name).is_dir():
            shutil.copytree(SOURCE / name, run_dir / name)
    model = Path(config['model'])
    runtime_config = {'network': {'host': '127.0.0.1', 'port': UPSTREAM_PORT, 'disable_auth': False,
        'allowed_origins': [], 'disable_fetch_requests': True, 'web_ui': False},
        'logging': {'log_prompt': False, 'log_requests': False, 'log_generation_params': False,
                    'log_chat_completion_requests': False, 'log_live_status': False},
        'model': {'model_dir': str(model.parent), 'model_name': model.name, 'backend': 'exllamav3',
            'max_seq_len': config['context'], 'cache_size': config['context'], 'cache_mode': config['cache_mode'],
            'gpu_split_auto': not bool(config.get('gpu_split')), 'gpu_split':config.get('gpu_split',[]),
            'tensor_parallel': config['tensor_parallel'], 'tensor_parallel_backend': 'native',
            'cpu_moe_offload_layers': config.get('cpu_offload_layers', 0),
            'cpu_moe_split_experts':config.get('cpu_split_experts',0),
            'cpu_moe_pinned_arena':bool(config.get('cpu_split_experts')),
            'chunk_size':config.get('chunk_size',2048),
            'warmup': False, 'inline_model_loading': False, 'max_batch_size': 1}}
    runtime_config['draft_model'] = {'draft_mode':'mtp' if config.get('mtp') else 'disabled',
        'draft_num_tokens':config.get('draft_tokens',2), 'draft_cache_mode':'Q8'}
    (run_dir / 'config.yml').write_text(json.dumps(runtime_config, ensure_ascii=False), encoding='utf-8')
    key, admin = secrets.token_hex(32), secrets.token_hex(32)
    (run_dir / 'api_tokens.yml').write_text(json.dumps({'api_key': key, 'admin_key': admin}), encoding='utf-8')
    sys.stdout = RedactedStream(sys.stdout, [key, admin])
    sys.stderr = RedactedStream(sys.stderr, [key, admin])
    if config['devices'] != 'auto':
        os.environ['CUDA_VISIBLE_DEVICES'] = config['devices']
    os.environ['EXL3_MOE_CPU_OFFLOAD'] = str(config.get('cpu_offload_layers', 0))
    os.environ['EXL3_MOE_CPU_SPLIT'] = str(config.get('cpu_split_experts',0))
    # Do not inherit profiling or a partial-layer override from a developer shell.
    for name in ('EXL3_SPLIT_PROF','EXL3_MOE_CPU_SPLIT_LAYERS','EXL3_MOE_CPU_SPLIT_STATS'):
        os.environ.pop(name,None)
    # TABBY_* overrides from a developer shell must not override the managed load.
    for name in list(os.environ):
        if name.startswith('TABBY_'):
            del os.environ[name]
    os.chdir(run_dir)
    sys.path.insert(0, str(SOURCE))
    sys.argv = [str(SOURCE / 'main.py')]
    heretic_state = {'enabled':config.get('heretic', False), 'applied':False}
    runtime_state = {}
    server = gateway(config, key, control, heretic_state, runtime_state)
    try:
        main_module = importlib.import_module('main')
        if Path(main_module.__file__).resolve() != (SOURCE/'main.py').resolve():
            raise RuntimeError('Unexpected TabbyAPI entry point')
        from model_heretic import install
        install(main_module, config, heretic_state, runtime_state)
        main_module.entrypoint()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
