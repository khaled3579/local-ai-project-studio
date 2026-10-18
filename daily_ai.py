"""Local control panel for the measured dual-3090 configuration."""
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen
from model_library import ModelLibrary, inspect_gguf, atomic_json
from app_maintenance import RotatingLog, console_logging
from project_model_info import details as model_details, DecodeTiming, parse_gpus, expert_override


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "daily"
DATA.mkdir(exist_ok=True)
(DATA / "logs").mkdir(exist_ok=True)
SETTINGS = DATA / "settings.json"
TOKEN = secrets.token_hex(24)
CONTROL_PORT = 18079
MODEL_PORT = 18080
MODEL = r"G:\lm_studio_models\unsloth\Qwen3.6-35B-A3B-MTP-GGUF\Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf"
MMPROJ = str(Path(MODEL).with_name("mmproj-F32.gguf"))
DEFAULT = {"context": 8192, "mtp": True}
LIBRARY = ModelLibrary(DATA / 'model-library.json', r'G:\lm_studio_models')

def validate(value, stored=False):
    if not isinstance(value, dict) or type(value.get("context")) is not int or type(value.get("mtp")) is not bool:
        raise ValueError("Expected context as an integer and mtp as a boolean")
    if not 512 <= value['context'] <= 2_147_483_647:
        raise ValueError('أدخل طول سياق صحيحًا بين 512 و2,147,483,647 رمزًا.')
    if value.get('model_id'):
        model = LIBRARY.resolve(value['model_id'])
    else:
        path = value.get('model', MODEL)
        model = next((m for m in LIBRARY.models if m['path'] == path), None)
        if model is None:
            if not stored and path != MODEL:
                raise ValueError('اختر النموذج من القائمة أولًا.')
            model = dict(inspect_gguf(path), path=path, projectors=[str(p) for p in Path(path).parent.glob('*.gguf') if 'mmproj' in p.name.lower()])
    limit = model.get('declared_context')
    if type(limit) is int and value['context'] > limit:
        raise ValueError(f'السياق المطلوب أكبر من السعة المعلنة في ملف النموذج: {limit:,}.')
    if value['mtp'] and not model['mtp']:
        raise ValueError('ملف النموذج لا يحتوي على أوزان MTP المدعومة؛ عطّل الخيار لهذا النموذج.')
    projectors = model.get('projectors', [])
    projector = value.get('projector', projectors[0] if len(projectors) == 1 else '')
    if projector and projector not in projectors:
        raise ValueError('ملف الرؤية المساعد لا ينتمي إلى هذا النموذج.')
    result={'context': value['context'], 'mtp': value['mtp'], 'model': model['path'], 'projector': projector}
    if value.get('experts_used') is not None:
        expert_override(model,value['experts_used'])
        result['experts_used']=value['experts_used']
    return result

def get_json(path, timeout=2):
    with urlopen(f"http://127.0.0.1:{MODEL_PORT}{path}", timeout=timeout) as response:
        return json.load(response)

def port_available(port):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

class ModelManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.process = None
        self.log_file = None
        self.log_thread = None
        self.log_error = None
        self.active = None
        self.target = None
        self.status = "stopped"
        self.error = None
        self.started = None
        self.log_path = None
        self.generation = 0
        self.gpus = []
        self.gpu_updated = None
        self.gpu_error = None
        self.props = {}
        self.generating = False
        self.model_details = {}
        self.decode_timing = DecodeTiming()
        try:
            self.saved = validate(json.loads(SETTINGS.read_text(encoding="utf-8")), stored=True)
        except (OSError, ValueError):
            self.saved = DEFAULT.copy()
        threading.Thread(target=self.sample, daemon=True).start()

    def sample(self):
        while True:
            try:
                p = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=6, creationflags=subprocess.CREATE_NO_WINDOW)
                if p.returncode:raise ValueError('تعذرت قراءة بطاقات NVIDIA')
                values = parse_gpus(p.stdout)
                if not values:raise ValueError('لم تصل قراءات بطاقات NVIDIA')
                with self.lock:
                    self.gpus = values
                    self.gpu_updated,self.gpu_error=time.time(),None
                    if self.status == "ready" and self.process and self.process.poll() is not None:
                        self.status = "failed"
                        self.error = "توقف خادم النموذج. يمكنك تشغيله مجددًا من اللوحة."
                        self.active = None
                if self.status == "ready":
                    try:
                        generating = any(slot.get("is_processing") for slot in get_json("/slots", 2))
                        with self.lock:
                            self.generating = generating
                    except (OSError, URLError, ValueError):
                        pass
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                with self.lock:self.gpu_error=str(error)
            time.sleep(3)

    def snapshot(self):
        with self.lock:
            return {"app": "daily3090", "status": self.status, "active": self.active, "target": self.target,
                    "saved": self.saved, "error": self.error, "generation": self.generation,
                    "pid": self.process.pid if self.process and self.process.poll() is None else None,
                    "elapsed_seconds": round(time.monotonic() - self.started, 1) if self.started else None,
                    "log": str(self.log_path) if self.log_path else None, "log_error":self.log_error, "gpus": self.gpus,
                    "gpu_updated":self.gpu_updated,"gpu_stale":not self.gpu_updated or time.time()-self.gpu_updated>10,"gpu_error":self.gpu_error,
                    "actual_context": self.props.get("default_generation_settings", {}).get("n_ctx"),
                    "vision": self.props.get("modalities", {}).get("vision", False),
                    "generating": self.generating,
                    "model_info": ({**self.model_details, 'mtp_enabled':self.active.get('mtp'),
                        'experts_active':self.decode_timing.experts_used if self.decode_timing.experts_used is not None else self.model_details.get('experts_active'),
                        'experts_override':self.active.get('experts_used'),
                        'context':self.props.get('default_generation_settings',{}).get('n_ctx'),
                        'speed':self.decode_timing.snapshot()} if self.active else None),
                    "chat_url": f"http://127.0.0.1:{MODEL_PORT}"}

    def begin(self, config=None):
        try:
            with urlopen('http://127.0.0.1:18078/api/state', timeout=2) as response:
                studio = json.load(response)
            if (studio.get('importing') or (studio.get('repair_run') or {}).get('status') == 'running'
                    or (studio.get('job') or {}).get('status') in ('running', 'queued')):
                raise RuntimeError('أوقف سلسلة المشروع مؤقتًا قبل تغيير النموذج أو إيقافه.')
        except (OSError, URLError, ValueError):
            pass
        if config is not None:
            config = validate(config, stored=config == self.saved)
        if self.status == "ready" and self.process and self.process.poll() is None:
            try:
                if any(slot.get("is_processing") for slot in get_json("/slots", 2)):
                    raise RuntimeError("انتظر اكتمال الرد الحالي قبل تغيير النموذج أو إيقافه.")
            except (URLError, OSError):
                raise RuntimeError("تعذّر التحقق من انتهاء الرد. حاول مجددًا بعد لحظات.")
        if not self.operation.acquire(blocking=False):
            raise RuntimeError("إعادة التحميل جارية؛ انتظر اكتمالها.")
        with self.lock:
            self.status = "loading" if config else "stopping"
            self.target = config
            self.error = None
            self.started = time.monotonic()
        threading.Thread(target=self.change, args=(config,), daemon=True).start()

    def stop_owned(self):
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        self.process = None
        self.props = {}
        if self.log_thread:
            self.log_thread.join(timeout=5)
            self.log_thread = None
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        self.active = None
        self.generating = False
        self.model_details = {}
        self.decode_timing = DecodeTiming()

    def launch(self, config):
        if not port_available(MODEL_PORT):
            raise RuntimeError(f"المنفذ {MODEL_PORT} مستخدم ببرنامج آخر. لم يتم إيقافه.")
        executable = ROOT / "runtime/llama-server.exe"
        if not executable.exists() or not Path(config['model']).exists():
            raise RuntimeError("ملف المحرك أو النموذج غير موجود في مساره.")
        with LIBRARY.lock:
            info=next((m for m in LIBRARY.models if m['path']==config['model']),None)
        info=info or inspect_gguf(config['model'])
        with self.lock:
            self.model_details=model_details(info,config['model'])
            self.decode_timing=DecodeTiming()
        command = [str(executable), "-m", config['model'], "-ngl", "99", "-sm", "layer", "-ts", "1,1",
                   "-c", str(config["context"]), "-b", "512", "-ub", "512", "-fa", "on",
                   "-ctk", "q8_0", "-ctv", "q8_0", "-t", "8", "-tb", "16", "--fit", "off", "-np", "1",
                   "--no-context-shift", "--host", "127.0.0.1", "--port", str(MODEL_PORT),
                   "--temp", "0", "--cors-origins", "localhost", "-lv", "4"]
        command += ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"] if config["mtp"] else ["--spec-type", "none"]
        command += expert_override(info,config.get('experts_used'))
        if config.get('projector'):
            command += ["--mmproj", config['projector'], "--image-max-tokens", "2048", "--mmproj-device", "CUDA1"]
        self.log_path = DATA / "logs" / f"{time.strftime('%Y%m%d-%H%M%S')}-ctx{config['context']}-mtp{int(config['mtp'])}.log"
        self.log_error = None
        self.log_file = RotatingLog(self.log_path)
        env = os.environ.copy()
        cuda_path = env.get("CUDA_PATH")
        if cuda_path:
            env["PATH"] = str(Path(cuda_path) / "bin") + os.pathsep + env.get("PATH", "")
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        pipe=self.process.stdout;sink=self.log_file
        def capture_log():
            try:
                while True:
                    block=pipe.read1(65536)
                    if not block:break
                    with self.lock:self.decode_timing.feed(block)
                    try:sink.write(block)
                    except OSError as error:
                        # Drain the pipe even when storage fails so generation cannot deadlock.
                        self.log_error=str(error)
            finally:pipe.close()
        self.log_thread=threading.Thread(target=capture_log,daemon=True);self.log_thread.start()
        for _ in range(600):
            if self.process.poll() is not None:
                raise RuntimeError(f"تعذّر تحميل النموذج. رمز الخروج: {self.process.returncode}. راجع سجل التشغيل.")
            try:
                if get_json("/health", 1).get("status") == "ok":
                    props = get_json("/props", 3)
                    actual = props.get("default_generation_settings", {}).get("n_ctx")
                    if actual != config["context"]:
                        raise RuntimeError(f"طول السياق الفعلي {actual} لا يطابق المطلوب {config['context']}.")
                    self.props = props
                    if config.get('experts_used') is not None and self.decode_timing.experts_used!=config['experts_used']:
                        raise RuntimeError('لم يؤكد سجل المحرك تطبيق عدد الخبراء المطلوب؛ لم يُعتمد الإعداد')
                    self.active = config.copy()
                    return
            except (URLError, OSError):
                pass
            time.sleep(0.5)
        raise TimeoutError("انتهت مهلة تحميل النموذج.")

    def change(self, config):
        previous = self.saved.copy() if self.active else None
        try:
            self.stop_owned()
            if config is None:
                with self.lock:
                    self.status = "stopped"
                    self.started = None
                return
            self.launch(config)
            atomic_json(SETTINGS, config)
            with LIBRARY.lock:
                for model in LIBRARY.models:
                    if model['path'] == config['model']:
                        model['load_status'] = 'تم تحميله بنجاح'
            with self.lock:
                self.saved = config.copy()
                self.status = "ready"
                self.generation += 1
        except Exception as error:
            self.stop_owned()
            message = str(error)
            if previous is not None and config != previous:
                try:
                    self.launch(previous)
                    with self.lock:
                        self.status = "ready"
                        self.error = "لم تنجح الإعدادات المطلوبة؛ أُعيد تشغيل آخر إعداد ناجح. " + message
                        self.generation += 1
                except Exception as recovery_error:
                    with self.lock:
                        self.status = "failed"
                        self.error = message + " / " + str(recovery_error)
            else:
                with self.lock:
                    self.status = "failed"
                    self.error = message
        finally:
            self.operation.release()

MANAGER = ModelManager()

def pick_models():
    with LIBRARY.lock:
        if LIBRARY.picking:
            raise RuntimeError('مستعرض المجلدات مفتوح بالفعل.')
        LIBRARY.picking, LIBRARY.error = True, None
    def work():
        try:
            result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'model_folder_picker.py'), LIBRARY.folder], capture_output=True, text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or 'تعذّر فتح مستعرض المجلدات.')
            if result.stdout.strip():
                LIBRARY.begin_scan(result.stdout.strip())
        except Exception as error:
            LIBRARY.error = str(error)
        finally:
            LIBRARY.picking = False
    threading.Thread(target=work, daemon=True).start()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionError):pass

    def do_GET(self):
        if self.path == "/api/state":
            state = MANAGER.snapshot()
            library = LIBRARY.snapshot()
            state['library'] = {k:v for k,v in library.items() if k not in ('models', 'errors')}
            self.send(state)
        elif self.path == '/api/library':
            self.send(LIBRARY.snapshot())
        elif self.path == '/api/client-session':
            if (self.headers.get('X-Local-Session') != 'refresh'
                    or self.headers.get('Host') not in (f'127.0.0.1:{CONTROL_PORT}', f'localhost:{CONTROL_PORT}')
                    or self.headers.get('Origin') not in (None, f'http://127.0.0.1:{CONTROL_PORT}', f'http://localhost:{CONTROL_PORT}')
                    or self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none')):
                self.send({'error': 'request rejected'}, 403)
            else:
                self.send({'token': TOKEN})
        elif self.path == "/":
            body = (ROOT / "daily_models.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionError):pass
        else:
            self.send({"error": "not found"}, 404)

    def do_POST(self):
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{CONTROL_PORT}", f"http://localhost:{CONTROL_PORT}"):
            self.send({"error": "origin rejected"}, 403)
            return
        if not hmac.compare_digest(self.headers.get("X-Local-Token", ""), TOKEN):
            self.send({"error": "token rejected", "code": "invalid_local_token"}, 403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 4096 or size < 0:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError('Expected a JSON object')
            if self.path == "/api/apply":
                MANAGER.begin(validate(payload))
            elif self.path == "/api/stop":
                MANAGER.begin(None)
            elif self.path == '/api/library/scan':
                LIBRARY.begin_scan(payload.get('folder'))
            elif self.path == '/api/library/pick':
                pick_models()
            else:
                self.send({"error": "not found"}, 404)
                return
            self.send(MANAGER.snapshot(), 202)
        except ValueError as error:
            self.send({"error": str(error)}, 400)
        except RuntimeError as error:
            self.send({"error": str(error)}, 409)

def main():
    console_logging(ROOT,"manager")
    server = ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
    LIBRARY.begin_scan()
    if '--no-autostart' not in sys.argv:
        try:
            MANAGER.begin(MANAGER.saved)
        except (OSError, ValueError, RuntimeError) as error:
            MANAGER.status, MANAGER.error = 'failed', str(error)
    print(f"Daily AI: http://127.0.0.1:{CONTROL_PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        MANAGER.stop_owned()
        server.server_close()

if __name__ == "__main__":
    main()
