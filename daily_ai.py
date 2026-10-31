"""Local control panel for the measured dual-3090 configuration."""
import hmac
import hashlib
from io import BufferedReader
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
from model_parameters import normalize, effective, arguments, SCHEMA, defaults as parameter_defaults
from model_load_planner import capabilities as load_capabilities, estimate as estimate_load
import model_tabby as tabby
import model_auto_profiles as auto_profiles
import model_llama_engines as llama_engines


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "daily"
DATA.mkdir(exist_ok=True)
(DATA / "logs").mkdir(exist_ok=True)
SETTINGS = DATA / "settings.json"
PROFILES = DATA / 'model-profiles.json'
TOKEN = secrets.token_hex(24)
CONTROL_PORT = 18079
MODEL_PORT = 18080
MODEL = r"G:\lm_studio_models\unsloth\Qwen3.6-35B-A3B-MTP-GGUF\Qwen3.6-35B-A3B-UD-Q8_K_XL.gguf"
MMPROJ = str(Path(MODEL).with_name("mmproj-F32.gguf"))
DEFAULT = {"context": 8192, "mtp": True}
LIBRARY = ModelLibrary(DATA / 'model-library.json', r'G:\lm_studio_models')

def validate(value, stored=False):
    if isinstance(value, dict) and value.get('engine', 'llamacpp') != 'llamacpp':
        return tabby.validate(value)
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
    result['parameters'] = normalize(value.get('parameters'))
    plan = value.get('load_plan')
    if plan is not None:
        if not isinstance(plan, dict) or set(plan) - {'profile', 'reserve_gib'} or plan.get('profile') not in {'custom','balanced','fast','long'}:
            raise ValueError('ملف أداء غير صالح')
        reserve = plan.get('reserve_gib', 1.5)
        if type(reserve) not in (int,float) or not 0 <= reserve <= 24:
            raise ValueError('هامش الذاكرة من صفر إلى 24 GiB لكل بطاقة')
        result['load_plan'] = {'profile': plan['profile'], 'reserve_gib': reserve}
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
        self.parameter_settings = None
        self.load_capabilities = {'status': 'loading', 'devices': [], 'features': {}}
        threading.Thread(target=self.discover_load_capabilities, daemon=True).start()
        self.profiles = {}
        self.profile_error = None
        if PROFILES.exists():
            try:
                saved_profiles = json.loads(PROFILES.read_text(encoding='utf-8'))
                if saved_profiles.get('version') != 1 or not isinstance(saved_profiles.get('models'), dict):
                    raise ValueError('ملف إعدادات النماذج غير صالح؛ لم يُستبدل')
                self.profiles = saved_profiles['models']
            except (OSError, ValueError, AttributeError) as error:
                self.profile_error = str(error)
        try:
            self.saved = validate(json.loads(SETTINGS.read_text(encoding="utf-8")), stored=True)
        except (OSError, ValueError):
            self.saved = DEFAULT.copy()
        self.llama_saved = self.saved.copy()
        self.tabby_saved = None
        self.engine_error = None
        self.gateway_token = None
        self.engine_identity = None
        try:
            if tabby.SAVED.exists():
                self.tabby_saved = tabby.read_object(tabby.SAVED)
            if tabby.SELECTION.exists():
                selection = tabby.read_object(tabby.SELECTION)
                if selection.get('version') != 1 or selection.get('engine') not in ('llamacpp', 'tabbyapi'):
                    raise ValueError('ملف اختيار المحرك غير صالح؛ حُفظ دون تعديل')
                if selection['engine'] == 'tabbyapi':
                    if self.tabby_saved is None:
                        raise ValueError('إعدادات TabbyAPI المحفوظة مفقودة')
                    self.saved = self.tabby_saved.copy()
        except (OSError, ValueError) as error:
            self.engine_error = str(error)
        threading.Thread(target=self.sample, daemon=True).start()

    def discover_load_capabilities(self):
        try:
            value = load_capabilities()
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            value = {'status': 'failed', 'error': str(error), 'devices': [], 'features': {}}
        with self.lock:
            self.load_capabilities = value

    def model_profile(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'engine', 'model', 'model_id'}:
            raise ValueError('طلب إعداد تلقائي غير صالح')
        engine = payload.get('engine', 'llamacpp')
        if engine == 'tabbyapi':
            model = tabby.model_info(payload.get('model', ''))
            legacy = self.tabby_saved if self.tabby_saved and self.tabby_saved.get('model') == model['path'] else None
        elif engine == 'llamacpp':
            with LIBRARY.lock:
                model = LIBRARY.resolve(payload['model_id']) if payload.get('model_id') else next((m for m in LIBRARY.models if m['path'] == payload.get('model')), None)
            if model is None:raise ValueError('اختر نموذج GGUF من المكتبة')
            legacy = self.profiles.get(model['path'])
            if self.llama_saved.get('model') == model['path']:legacy = self.llama_saved
        else:
            raise ValueError('محرك غير مدعوم')
        inventory = self.load_capabilities if engine == 'tabbyapi' else load_capabilities(llama_engines.select(model))
        result = auto_profiles.profile(engine, model, inventory, legacy=legacy)
        result['defaults'] = validate(result['defaults'], stored=True)
        if result['saved']:
            try:result['saved'] = validate(result['saved'], stored=True)
            except (ValueError, OSError) as error:
                result['saved'] = None
                result['warnings'].append('التخصيص السابق محفوظ لكنه يحتاج مراجعة: ' + str(error))
        result['recommended'] = result['saved'] or result['defaults']
        result['has_saved'] = bool(result['saved'])
        return result

    def load_preview(self, payload):
        config = validate(payload)
        if config.get('engine') == 'tabbyapi':
            raise ValueError('تقدير GGUF لا ينطبق على EXL3؛ تقدير TabbyAPI غير متحقق حتى السماح بالقياس')
        with LIBRARY.lock:
            model = next((m for m in LIBRARY.models if m['path'] == config['model']), None)
        if model is None:
            raise ValueError('انتظر اكتمال فهرسة النموذج')
        with self.lock:
            inventory, gpus, active = self.load_capabilities, list(self.gpus), self.active
            stale = not self.gpu_updated or time.time() - self.gpu_updated > 10
        inventory = load_capabilities(llama_engines.select(model))
        return estimate_load(model, config, effective(dict(model, path=config['model']), config), gpus, inventory, active=active, gpu_stale=stale)

    def reconcile_exit(self):
        """Caller holds self.lock; loading/stopping belong to change()."""
        if self.status != "ready" or self.process is None:
            return
        code = self.process.poll()
        if code is None:
            return
        self.status = "failed"
        self.error = f"توقف خادم النموذج (رمز الخروج: {code}). يمكنك تشغيله مجددًا من اللوحة. راجع سجل التشغيل."
        self.active = None
        self.generating = False
        self.props = {}
        self.model_details = {}
        self.decode_timing = DecodeTiming()

    def sample(self):
        while True:
            # Lifecycle detection must also work when NVIDIA telemetry fails.
            with self.lock:
                self.reconcile_exit()
                process = self.process if self.status == "ready" else None
            if process is not None:
                try:
                    generating = any(slot.get("is_processing") for slot in get_json("/slots", 2))
                    with self.lock:
                        self.reconcile_exit()
                        # A late response from the previous server cannot update a new one.
                        if self.status == "ready" and self.process is process:
                            self.generating = generating
                except (OSError, URLError, ValueError):
                    with self.lock:
                        self.reconcile_exit()
                    # A live server's busy state is not cleared on a network error.
                    # begin() independently verifies /slots before any model change.
            try:
                p = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=6, creationflags=subprocess.CREATE_NO_WINDOW)
                if p.returncode:raise ValueError('تعذرت قراءة بطاقات NVIDIA')
                values = parse_gpus(p.stdout)
                if not values:raise ValueError('لم تصل قراءات بطاقات NVIDIA')
                with self.lock:
                    self.gpus = values
                    self.gpu_updated,self.gpu_error=time.time(),None
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                with self.lock:self.gpu_error=str(error)
            time.sleep(3)

    def snapshot(self):
        with self.lock:
            self.reconcile_exit()
            return {"app": "daily3090", "status": self.status, "active": self.active, "target": self.target,
                    "load_capabilities": self.load_capabilities,
                    "engine": (self.active or self.target or self.saved).get('engine', 'llamacpp'),
                    "engines": [{'id':'llamacpp','name':'llama.cpp / GGUF','installed':True}, tabby.availability()],
                    "engine_error": self.engine_error, "engine_identity": self.engine_identity,
                    "heretic": self.props.get('studio_heretic'),
                    "tabby_runtime": self.props.get('studio_runtime'),
                    "llama_saved": self.llama_saved, "tabby_saved": self.tabby_saved,
                    "saved": self.saved, "error": self.error, "generation": self.generation,
                    "parameters": self.parameter_settings if self.active else None, "profile_error": self.profile_error,
                    "pid": self.process.pid if self.process and self.process.poll() is None else None,
                    "elapsed_seconds": round(time.monotonic() - self.started, 1) if self.started else None,
                    "log": str(self.log_path) if self.log_path else None, "log_error":self.log_error, "gpus": self.gpus,
                    "gpu_updated":self.gpu_updated,"gpu_stale":not self.gpu_updated or time.time()-self.gpu_updated>10,"gpu_error":self.gpu_error,
                    "actual_context": self.props.get("default_generation_settings", {}).get("n_ctx"),
                    "vision": self.props.get("modalities", {}).get("vision", False),
                    "generating": bool(self.status == "ready" and self.process and self.process.poll() is None and self.generating),
                    "model_info": ({**self.model_details, 'mtp_enabled':self.active.get('mtp'),
                        'experts_active':self.decode_timing.experts_used if self.decode_timing.experts_used is not None else self.model_details.get('experts_active'),
                        'experts_override':self.active.get('experts_used'),
                        'context':self.props.get('default_generation_settings',{}).get('n_ctx'),
                        'speed':self.decode_timing.snapshot() if self.active.get('engine') != 'tabbyapi' else {'tokens_per_second':None,'phase':'unmeasured','source':'TabbyAPI — لم يُقَس'}} if self.active else None),
                    "chat_url": f"http://127.0.0.1:{MODEL_PORT}"}

    def begin(self, config=None):
        if config is not None and self.profile_error:
            raise ValueError(self.profile_error)
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
            if config.get('engine') == 'tabbyapi':
                config = tabby.validate(config, require_runtime=True)
                if not port_available(tabby.UPSTREAM_PORT) and not (self.active and self.active.get('engine') == 'tabbyapi' and self.process and self.process.poll() is None):
                    raise RuntimeError('منفذ TabbyAPI مشغول؛ لم يُوقف أي خادم خارجي')
        if self.status == "ready" and self.process and self.process.poll() is None:
            try:
                if any(slot.get("is_processing") for slot in get_json("/slots", 2)):
                    raise RuntimeError("انتظر اكتمال الرد الحالي قبل تغيير النموذج أو إيقافه.")
            except (URLError, OSError):
                raise RuntimeError("تعذّر التحقق من انتهاء الرد. حاول مجددًا بعد لحظات.")
        if not self.operation.acquire(blocking=False):
            raise RuntimeError("إعادة التحميل جارية؛ انتظر اكتمالها.")
        try:
            if self.active and self.active.get('engine') == 'tabbyapi' and self.process and self.process.poll() is None:
                from urllib.request import Request
                request = Request(f'http://127.0.0.1:{MODEL_PORT}/studio/quiesce', data=b'{}',
                    headers={'Content-Type':'application/json','X-Engine-Control':self.gateway_token or ''})
                with urlopen(request, timeout=5) as response:
                    if not json.load(response).get('quiesced'):
                        raise RuntimeError('تعذّر تأمين إيقاف المحرك الحالي')
        except (OSError, ValueError) as error:
            self.operation.release()
            raise RuntimeError('لم يُبدل المحرك أثناء الرد: ' + str(error))
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
        if config.get('engine') == 'tabbyapi':
            return self.launch_tabby(config)
        self.engine_identity = None
        if not port_available(MODEL_PORT):
            raise RuntimeError(f"المنفذ {MODEL_PORT} مستخدم ببرنامج آخر. لم يتم إيقافه.")
        with LIBRARY.lock:
            info=next((m for m in LIBRARY.models if m['path']==config['model']),None)
        info=info or inspect_gguf(config['model'])
        executable = llama_engines.select(info)
        if not executable.exists() or not Path(config['model']).exists():
            raise RuntimeError('ملف المحرك أو النموذج غير موجود في مساره.')
        self.engine_identity = llama_engines.identity(info)
        with self.lock:
            self.model_details=model_details(info,config['model'])
            self.decode_timing=DecodeTiming()
        self.parameter_settings = effective(dict(info, path=config['model']), config)
        runtime = self.parameter_settings['values']['runtime']
        chosen = runtime.get('devices', 'auto')
        if chosen != 'auto':
            inventory = load_capabilities(executable)
            available = {row['id'] for row in inventory['devices']}
            if not inventory['features'].get('--device') or any(name not in available for name in chosen.split(',')):
                raise ValueError('إحدى البطاقات المختارة غير متاحة في المحرك الحالي؛ راجع قائمة CUDA')
            if runtime['main_gpu'] >= len(chosen.split(',')):
                raise ValueError('البطاقة الأساسية خارج قائمة البطاقات المختارة')
        command = [str(executable), "-m", config['model'],
                   "-c", str(config["context"]), "--fit", "off", "-np", "1",
                   "--no-context-shift", "--host", "127.0.0.1", "--port", str(MODEL_PORT),
                   "--cors-origins", "localhost", "-lv", "4"]
        command += arguments(self.parameter_settings)
        if info.get('architecture') == 'clover_moe':
            command += ['--reasoning-format', 'deepseek', '--chat-template-file', str(executable.parent / 'clover-chat-template.jinja')]
        command += ["--spec-type", "draft-mtp", "--spec-draft-n-max", "3"] if config["mtp"] else ["--spec-type", "none"]
        command += expert_override(info,config.get('experts_used'))
        if config.get('projector'):
            command += ["--mmproj", config['projector'], "--image-max-tokens", "2048", "--mmproj-device", chosen.split(',')[-1] if chosen != 'auto' else "CUDA1"]
        self.log_path = DATA / "logs" / f"{time.strftime('%Y%m%d-%H%M%S')}-ctx{config['context']}-mtp{int(config['mtp'])}.log"
        self.log_error = None
        self.log_file = RotatingLog(self.log_path)
        env = os.environ.copy()
        cuda_path = env.get("CUDA_PATH")
        if cuda_path:
            env["PATH"] = str(Path(cuda_path) / "bin") + os.pathsep + env.get("PATH", "")
        self.spawn(command, env)
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

    def spawn(self, command, env):
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        pipe=self.process.stdout;sink=self.log_file
        if not isinstance(pipe, BufferedReader):
            raise RuntimeError('تعذّر فتح قناة سجل المحرك الثنائية')
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

    def launch_tabby(self, config):
        config = tabby.validate(config, require_runtime=True)
        if not port_available(MODEL_PORT) or not port_available(tabby.UPSTREAM_PORT):
            raise RuntimeError('منفذ المحرك مشغول؛ لم يُوقف أي خادم غير مملوك للبرنامج')
        info = tabby.model_info(config['model'])
        text = info['config'].get('text_config') or info['config']
        self.model_details = {'name':info['name'], 'filename':info['name'], 'architecture':info['architecture'],
            'bytes':None, 'parameters':None, 'declared_context':info['declared_context'],
            'mtp_supported':bool(text.get('mtp_num_hidden_layers')), 'experts_total':text.get('num_experts'), 'experts_active':text.get('num_experts_per_tok'), 'engine':'tabbyapi'}
        self.parameter_settings = tabby.settings(config)
        self.engine_identity = tabby.identity(config)
        self.gateway_token = secrets.token_hex(32)
        self.log_path = DATA / 'logs' / (time.strftime('%Y%m%d-%H%M%S') + '-tabby.log')
        self.log_error = None
        self.log_file = RotatingLog(self.log_path)
        self.spawn(tabby.command({**config, '_control_token':self.gateway_token}), os.environ.copy())
        for _ in range(600):
            if self.process.poll() is not None:
                raise RuntimeError('توقف TabbyAPI أثناء التحميل؛ راجع السجل. لم يُعد إرسال أي طلب')
            try:
                if get_json('/health', 1).get('status') == 'ok':
                    props = get_json('/props', 3)
                    if props.get('default_generation_settings', {}).get('n_ctx') != config['context']:
                        raise RuntimeError('السياق الفعلي لا يطابق إعداد TabbyAPI المطلوب')
                    if Path(props.get('model_path', '')).resolve() != Path(config['model']).resolve():
                        raise RuntimeError('هوية النموذج الفعلي لا تطابق المطلوب؛ لم يُعتمد التحميل')
                    if config.get('heretic') and not (props.get('studio_heretic') or {}).get('applied'):
                        raise RuntimeError('لم يؤكد المحرك تطبيق ملحق Heretic؛ لم يُعتمد التحميل')
                    runtime = props.get('studio_runtime') or {}
                    if config.get('mtp') and not runtime.get('mtp_loaded'):
                        raise RuntimeError('لم يؤكد المحرك تحميل MTP؛ لم يُعتمد الإعداد')
                    if config.get('cpu_split_experts') and runtime.get('cpu_experts_per_layer') != config['cpu_split_experts']:
                        raise RuntimeError('لم يؤكد المحرك تقسيم الخبراء المطلوب؛ لم يُعتمد الإعداد')
                    self.props = props
                    self.engine_identity['loaded_template_sha256'] = hashlib.sha256(props.get('chat_template','').encode()).hexdigest()
                    self.active = config.copy()
                    return
            except (URLError, OSError):
                pass
            time.sleep(.5)
        raise TimeoutError('انتهت مهلة تحميل TabbyAPI؛ حُفظ الإخفاق دون تبديل صامت')

    def commit_config(self, config):
        # Engine-specific profiles, one explicit selection. Restore all written
        # files on a partial persistence failure; never migrate conversations.
        is_tabby = config.get('engine') == 'tabbyapi'
        profiles = {**self.profiles, config['model']:config.copy()} if not is_tabby else self.profiles
        writes = [(tabby.SAVED, config)] if is_tabby else [(PROFILES, {'version':1,'models':profiles}), (SETTINGS, config)]
        writes.append((tabby.SELECTION, {'version':1,'engine':'tabbyapi' if is_tabby else 'llamacpp'}))
        engine = 'tabbyapi' if is_tabby else 'llamacpp'
        model = tabby.model_info(config['model']) if is_tabby else dict(inspect_gguf(config['model']), path=config['model'])
        inventory = self.load_capabilities if is_tabby else load_capabilities(llama_engines.select(model))
        writes.append((auto_profiles.STORE, auto_profiles.updated_store(engine, model, inventory, config)))
        originals = {path:json.loads(path.read_text(encoding='utf-8')) if path.exists() else None for path,_ in writes}
        try:
            for path,value in writes:
                atomic_json(path, value)
        except OSError:
            for path,value in originals.items():
                if value is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_json(path, value)
            raise
        if is_tabby:
            self.tabby_saved = config.copy()
        else:
            self.llama_saved = config.copy()
        self.engine_error = None
        return profiles

    def change(self, config):
        previous = self.active.copy() if self.active else None
        try:
            self.stop_owned()
            if config is None:
                with self.lock:
                    self.status = "stopped"
                    self.started = None
                return
            self.launch(config)
            profiles = self.commit_config(config)
            with LIBRARY.lock:
                for model in LIBRARY.models:
                    if model['path'] == config['model']:
                        model['load_status'] = 'تم تحميله بنجاح'
            with self.lock:
                self.saved = config.copy()
                self.profiles = profiles
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
    def log_message(self, format: str, *args: object) -> None:
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
            library = LIBRARY.snapshot()
            with MANAGER.lock:
                profiles = dict(MANAGER.profiles)
            library['models'] = [dict(model, parameter_defaults=parameter_defaults(model), saved_profile=profiles.get(model['path'])) for model in library['models']]
            self.send(library)
        elif self.path == '/api/parameter-schema':
            self.send(SCHEMA)
        elif self.path in ('/model-parameters.js', '/model-load-planner.js', '/model-engine.js', '/model-auto-profiles.js'):
            name = {'/model-parameters.js':'model_parameters_client.js', '/model-load-planner.js':'model_load_planner_client.js', '/model-engine.js':'model_engine_client.js', '/model-auto-profiles.js':'model_auto_profiles_client.js'}[self.path]
            body = (ROOT / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError, ConnectionError):pass
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
            if size > 1024 * 1024 or size < 0:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError('Expected a JSON object')
            if self.path == '/api/model-profile':
                self.send(MANAGER.model_profile(payload))
                return
            if self.path == "/api/apply":
                MANAGER.begin(validate(payload))
            elif self.path == '/api/load-preview':
                self.send(MANAGER.load_preview(payload))
                return
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
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.send({"error": str(error)}, 400)
        except RuntimeError as error:
            self.send({"error": str(error)}, 409)

def main():
    console_logging(ROOT,"manager")
    server = ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), Handler)
    LIBRARY.begin_scan()
    if '--no-autostart' not in sys.argv:
        try:
            if MANAGER.engine_error:
                raise ValueError(MANAGER.engine_error)
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
