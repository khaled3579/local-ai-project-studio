"""Project file operations and real local validation for Daily AI."""
import ast
import hashlib
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from project_history import History, locked, recorded_command
from app_maintenance import ensure_schema
from project_validation import VERSION as VALIDATION_VERSION, COMMAND_ENV, python_tests, node_tests, node_syntax, tkinter_startup

EXCLUDED = {".local-ai", ".venv", "venv", ".git", "node_modules", "__pycache__", ".pytest_cache"}

def atomic_replace(source, target):
    # Windows indexers/scanners can briefly hold a newly written file open.
    for attempt in range(6):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))

def tool(name, description, properties=None, required=None):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}}

STRING = {"type": "string"}
TOOLS = [
    tool("list_files", "List actual files in the selected project. Start by inspecting the project."),
    tool("read_file", "Read a UTF-8 project file. Returns its SHA256, required before overwriting existing files.", {"path": STRING}, ["path"]),
    tool("write_file", "Create or update a complete file inside the selected project. Existing files require reading first, then expected_sha256='@last_read' (the controller supplies the exact observed hash and still rejects stale edits). Parent folders are created. Save code with this tool.", {"path": STRING, "content": STRING, "expected_sha256": STRING}, ["path", "content"]),
    tool('patch_file', "Repair an existing file with ONE exact text replacement instead of rewriting it. Read current file first, then use expected_sha256='@last_read'. old_text must occur exactly once. Python syntax is checked before committing; failed patches do not change the file. Prefer this for small fixes.", {'path':STRING,'old_text':STRING,'new_text':STRING,'expected_sha256':STRING}, ['path','old_text','new_text','expected_sha256']),
    tool("run_command", "Execute a noninteractive project command with its cwd set to the project. Python uses the project .venv. Use for diagnostics/tests, not persistent GUI apps. Use an argv array, not a shell expression.", {"argv": {"type": "array", "items": STRING}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}}, ["argv"]),
    tool("validate_project", "Validate every detected Python and JavaScript layer together. Python unittest and native node:test require fresh execution receipts with actual passes; skipped-only tests and a successful npm exit are not passes. Unsupported test runners fail explicitly. Preserve existing tests and frameworks; never remove a language or weaken tests to satisfy validation. Writes a versioned report required before delivery."),
    tool("run_project", "Run a Python, JavaScript or HTML project entry file after successful validation. HTML starts a loopback preview and returns its URL. Only use when the user asks to run the application. GUI/long-running apps run in background and have a Stop button. For a short CLI example prefer run_command.", {"entry": STRING, "args": {"type": "array", "items": STRING}, "background": {"type": "boolean"}}, ["entry"]),
]
from project_toolhub import schemas as assistant_schemas, NAMES as ASSISTANT_NAMES, hub as assistant_hub
TOOLS.extend(assistant_schemas(tool, STRING))

class Workspace:
    def __init__(self, root, on_event=None):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir() or self.root == Path(self.root.anchor):
            raise ValueError("اختر مجلد مشروع موجودًا، وليس جذر قرص كامل.")
        self.meta = self.root / ".local-ai"
        if not self.meta.resolve().is_relative_to(self.root):
            raise ValueError("Project metadata must stay inside the selected project")
        self.meta.mkdir(exist_ok=True)
        ensure_schema(self.meta)
        self.event = on_event or (lambda *_: None)
        self.running = None
        self.run_log = None
        self.hash_cache = {}
        self.validation_pending = False
        self.history = History(self)

    def path(self, name):
        candidate = (self.root / str(name)).resolve()
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise ValueError("المسار يجب أن يكون ملفًا داخل مجلد المشروع المحدد.")
        if any(p in EXCLUDED for p in candidate.relative_to(self.root).parts):
            raise ValueError("هذا المسار مخصص لبيانات التشغيل أو الملفات الداخلية.")
        if os.name == "nt" and ":" in str(candidate.relative_to(self.root)):
            raise ValueError("Invalid project file path")
        return candidate

    def files(self):
        result = []
        for base, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in EXCLUDED and not (Path(base) / d).is_symlink() and (Path(base) / d).resolve().is_relative_to(self.root)]
            for name in files:
                p = Path(base) / name
                if p.is_symlink() or not p.resolve().is_relative_to(self.root):
                    continue
                result.append({"path": p.relative_to(self.root).as_posix(), "bytes": p.stat().st_size})
        return sorted(result, key=lambda v: v["path"])

    def fingerprint(self, force=False):
        values = []
        for item in self.files():
            p = self.root / item["path"]
            stat = p.stat()
            signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            cached = self.hash_cache.get(item["path"])
            if not force and cached and cached[0] == signature:
                digest = cached[1]
            else:
                with p.open("rb") as file:
                    digest = hashlib.file_digest(file, "sha256").hexdigest()
                self.hash_cache[item["path"]] = (signature, digest)
            values.append((item["path"], digest))
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def read(self, path):
        p = self.path(path)
        if p.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("اقرأ الملفات النصية التي يقل حجمها عن 2MB على أجزاء أصغر.")
        raw = p.read_bytes()
        return {"path": p.relative_to(self.root).as_posix(), "content": raw.decode("utf-8-sig"), "sha256": hashlib.sha256(raw).hexdigest()}

    @locked
    def write(self, path, content, expected_sha256=None):
        p = self.path(path)
        if not isinstance(content, str):
            raise ValueError("content must be text")
        if p.exists():
            old = p.read_bytes()
            if expected_sha256 != hashlib.sha256(old).hexdigest():
                raise ValueError("Read the current file first and pass its expected_sha256; the file may have changed.")
        self.history.checkpoint("قبل تعديل: " + str(path))
        if p.exists():
            backup = self.meta / "backups" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]) / p.relative_to(self.root)
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(old)
        p.parent.mkdir(parents=True, exist_ok=True)
        temporary = p.with_name(p.name + "." + uuid.uuid4().hex[:8] + ".tmp")
        content = content.replace("\r\n", "\n")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        atomic_replace(temporary, p)
        self.history.checkpoint("حفظ: " + str(path))
        self.event("file_written", {"path": p.relative_to(self.root).as_posix()})
        return {"saved": p.relative_to(self.root).as_posix(), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

    def python(self):
        executable = self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not executable.exists():
            self.event("environment", {"message": "إنشاء بيئة Python داخل المشروع"})
            p = subprocess.run([sys.executable, "-m", "venv", str(self.root / ".venv")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if p.returncode:
                raise RuntimeError(p.stderr)
        return str(executable)

    @recorded_command
    def command(self, argv, timeout_seconds=90):
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
            raise ValueError("argv must be a nonempty array of strings")
        original = list(argv)
        name = Path(argv[0]).name.lower()
        if name in {"python", "python3", "python.exe", "py"}:
            argv = [self.python(), *argv[1:]]
        elif name == "pytest":
            argv = [self.python(), "-m", "pytest", *argv[1:]]
        elif name in {"node", "node.exe", "npm", "npm.cmd", "npx", "dotnet", "go", "cargo"}:
            resolved = shutil.which(argv[0])
            if not resolved:
                raise ValueError(f"الأداة غير مثبتة: {argv[0]}")
            argv = [resolved, *argv[1:]]
            if resolved.lower().endswith(".cmd"):
                if any(re.search(r"[&|<>^%\r\n]", arg) for arg in argv):
                    raise ValueError("Shell expressions are not supported; pass plain command arguments")
                argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *argv]
        else:
            raise ValueError("استخدم python أو node أو npm أو dotnet أو go أو cargo بأسماء البرامج، دون أوامر shell.")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(COMMAND_ENV.get())
        started = time.monotonic()
        p = subprocess.Popen(argv, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if getattr(self, "process_observer", None):
            self.process_observer(p.pid)
        try:
            stdout, stderr = p.communicate(timeout=max(1, min(120, timeout_seconds)))
            result = {"argv": original, "exit_code": p.returncode, "stdout": stdout if len(stdout) <= 24000 else stdout[:12000] + "\n[output excerpt]\n" + stdout[-12000:], "stderr": stderr if len(stderr) <= 24000 else stderr[:12000] + "\n[output excerpt]\n" + stderr[-12000:], "seconds": round(time.monotonic() - started, 2)}
        except subprocess.TimeoutExpired as error:
            import psutil
            try:
                children = psutil.Process(p.pid).children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass
            p.kill()
            p.communicate()
            result = {"argv": original, "exit_code": None, "timed_out": True, "stdout": str(error.stdout or "")[-12000:], "stderr": str(error.stderr or "")[-12000:]}
        self.event("command", result)
        return result

    def validate_static_web(self, files, run_tests=True):
        """Validate standalone browser projects, including ones below the root."""
        checks = [node_syntax(self, files)]
        missing = []
        class Assets(HTMLParser):
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                ref = values.get('src') if tag in {'script', 'img'} else values.get('href') if tag == 'link' and values.get('rel') == 'stylesheet' else None
                if ref:
                    self.refs.append(ref)
        for item in files:
            if not item['path'].endswith(('.html', '.htm')):
                continue
            html = self.path(item['path']); parser = Assets();parser.refs = []
            parser.feed(html.read_text(encoding='utf-8-sig'))
            for ref in parser.refs:
                url = urlsplit(ref)
                if url.scheme or url.netloc or not url.path:
                    continue
                target = self.root / unquote(url.path).lstrip('/') if url.path.startswith('/') else html.parent / unquote(url.path)
                if not target.resolve().is_relative_to(self.root) or not target.is_file():
                    missing.append({'html': item['path'], 'asset': ref})
        checks.append({'name': 'web_local_assets', 'passed': not missing, 'missing': missing})
        if not run_tests:
            return checks
        tests = [v['path'] for v in files if re.search(r'(?:\.test|\.spec)\.(?:[cm]?js)$', v['path']) or re.search(r'(?:^|/)tests/test_[^/]+\.[cm]?js$', v['path'])]
        if tests:
            checks.append(node_tests(self, ['node', '--test', *tests], 'web_node_tests'))
        else:
            checks.append({'name': 'web_node_tests', 'passed': False, 'tests': 0,
                'error': 'هذا مشروع ويب ثابت HTML/JavaScript، حتى لو كان داخل مجلد فرعي. أضف اختبارات حقيقية بملف tests/*.test.cjs أو *.test.js باستخدام node:test وassert. يمكن تحميل سكربتات المتصفح في node:vm واختبار المحرك. ثم أعد validate_project. لا حاجة لإضافة Python أو حزمة npm غير ضرورية.'})
        return checks

    def _save_validation_report(self, report):
        destination = self.meta / 'last_validation.json'
        temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        atomic_replace(temporary, destination)

    @locked
    def validate(self):
        start_fingerprint = self.fingerprint(force=True)
        files = self.files()
        # A new failed/interrupted check must not leave a previous green report usable.
        self.validation_pending = True
        self._save_validation_report({'validator_version': VALIDATION_VERSION,
            'timestamp': time.time(), 'success': False, 'in_progress': True,
            'fingerprint': start_fingerprint,
            'checks': [{'name': 'validation_incomplete', 'passed': False,
                        'error': 'الفحص الجديد لم يكتمل؛ لا يُعتمد التقرير الناجح السابق.'}]})
        checks = []
        py = [item for item in files if item["path"].endswith((".py", ".pyw"))]
        if py:
            errors = []
            for item in py:
                try:
                    p = self.path(item["path"])
                    compile(p.read_text(encoding="utf-8-sig"), str(p), "exec")
                except (SyntaxError, UnicodeError, OSError) as error:
                    errors.append({"path": item["path"], "error": str(error)})
            checks.append({"name": "python_syntax", "passed": not errors, "files": len(py), "errors": errors})
            if not errors:
                checks.append(python_tests(self))
                checks.extend(tkinter_startup(self, py))
        has_manifest = (self.root / "package.json").exists()
        has_web = any(v['path'].endswith(('.html', '.htm', '.js', '.mjs', '.cjs')) for v in files)
        if has_web:
            checks.extend(self.validate_static_web(files, run_tests=not has_manifest))
        if has_manifest:
            manifest = json.loads((self.root / "package.json").read_text(encoding="utf-8-sig"))
            scripts = manifest.get("scripts", {})
            for script in ("test", "build"):
                if script in scripts:
                    if script == 'test':
                        checks.append(node_tests(self, ['npm', 'run', 'test'], 'npm_test'))
                    else:
                        result = self.command(['npm', 'run', 'build'], 120)
                        checks.append({'name': 'npm_build', 'passed': result['exit_code'] == 0, **result})
            if "test" not in scripts:
                checks.append({"name": "npm_test", "passed": False, "error": "لا يوجد test script. أضف اختبارات حقيقية."})
        if not checks:
            checks.append({"name": "supported_validation", "passed": False, "error": "الفحص التلقائي يدعم Python وNode حاليًا. يمكن تشغيل أدوات لغات أخرى بالأمر المناسب، لكن يلزم تقرير فحص صريح."})
        final_fingerprint = self.fingerprint(force=True)
        report = {"validator_version": VALIDATION_VERSION,
                  "timestamp": time.time(), "success": bool(checks) and all(c["passed"] for c in checks),
                  "checks": checks, "fingerprint": final_fingerprint, "source_unchanged_during_validation": start_fingerprint == final_fingerprint,
                  "scope": "syntax/build, project tests and detected Tkinter entry startup; not a guarantee of all behavior or GUI rendering"}
        if not report["source_unchanged_during_validation"]:
            report["success"] = False
        self._save_validation_report(report)
        self.validation_pending = False
        self.event("validation", report)
        self.history.checkpoint("فحص ناجح" if report["success"] else "فحص فاشل", report)
        return report

    def last_validation(self):
        try:
            report = json.loads((self.meta / "last_validation.json").read_text(encoding="utf-8"))
            report['validation_outdated'] = report.get('validator_version') != VALIDATION_VERSION
            report["current"] = (not report['validation_outdated'] and not self.validation_pending
                                 and not report.get('in_progress') and report["fingerprint"] == self.fingerprint())
            return report
        except (OSError, ValueError):
            return None

    @locked
    def run(self, entry, args=None, background=True):
        report = self.last_validation()
        # UI polling may use the stat cache; execution must verify current bytes.
        if not report or not report["success"] or not report["current"] or report['fingerprint'] != self.fingerprint(force=True):
            raise ValueError("افحص المشروع وأصلح الأخطاء قبل تشغيله: validate_project")
        p = self.path(entry)
        self.history.checkpoint("قبل تشغيل التطبيق")
        if p.suffix.lower() in {'.html', '.htm'}:
            if args:
                raise ValueError('معاينة HTML لا تحتاج وسائط تشغيل')
            if self.running and self.running.poll() is None:
                raise ValueError('التطبيق يعمل بالفعل؛ افتح المعاينة الحالية أو أوقفه أولًا')
            self.run_log = (self.meta / 'application.log').open('wb')
            self.running = subprocess.Popen([sys.executable, str(Path(__file__).with_name('project_preview.py')), '--root', str(self.root), '--entry', p.relative_to(self.root).as_posix()], cwd=self.root, stdout=self.run_log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if getattr(self, 'process_observer', None):
                self.process_observer(self.running.pid)
            for _ in range(100):
                log = (self.meta / 'application.log').read_text(encoding='utf-8', errors='replace')
                match = re.search(r'PREVIEW_URL=(http://127\.0\.0\.1:\d+/[^\r\n]*)', log)
                if match:
                    self.preview_url = match[1]
                    self.event('application_started', {'pid':self.running.pid, 'entry':entry, 'url':self.preview_url})
                    return {'started':True, 'pid':self.running.pid, 'entry':entry, 'url':self.preview_url}
                if self.running.poll() is not None:
                    raise ValueError('تعذر بدء معاينة الويب: ' + log[-1000:])
                time.sleep(.05)
            self.stop()
            raise ValueError('انتهت مهلة بدء معاينة الويب')
        if p.suffix.lower() not in {".py", ".pyw", ".js", ".mjs"}:
            raise ValueError("التشغيل المباشر يدعم ملفات Python وJavaScript حاليًا.")
        argv = [self.python(), str(p), *(args or [])] if p.suffix.lower() in {".py", ".pyw"} else [shutil.which("node") or "node", str(p), *(args or [])]
        if not background:
            return self.command(["python" if p.suffix.lower() in {".py", ".pyw"} else "node", str(p), *(args or [])])
        if self.running and self.running.poll() is None:
            raise ValueError("التطبيق يعمل بالفعل. أوقفه أولًا.")
        self.run_log = (self.meta / "application.log").open("wb")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        self.running = subprocess.Popen(argv, cwd=self.root, env=env, stdout=self.run_log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if getattr(self, "process_observer", None):
            self.process_observer(self.running.pid)
        # Popen success alone does not prove that the application started.
        for _ in range(10):
            time.sleep(.1)
            if self.running.poll() is not None:
                details=(self.meta/'application.log').read_text(encoding='utf-8',errors='replace')[-4000:]
                raise ValueError('Application exited during startup: '+details)
        self.event("application_started", {"pid": self.running.pid, "entry": entry})
        return {"started": True, "pid": self.running.pid, "entry": entry, "log": ".local-ai/application.log"}

    def stop(self):
        if hasattr(self,'assistant_tools'):self.assistant_tools.browser.close()
        if self.running and self.running.poll() is None:
            import psutil
            process = psutil.Process(self.running.pid)
            children = process.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            self.running.terminate()
            try:
                self.running.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.running.kill()
                self.running.wait(timeout=5)
        if self.run_log:
            self.run_log.close()
            self.run_log = None
        return {"stopped": True}

    @locked
    def patch(self, path, old_text, new_text, expected_sha256):
        value=self.read(path)
        if value['sha256']!=expected_sha256:raise ValueError('File changed; read current file again')
        if not isinstance(old_text,str) or not old_text or value['content'].count(old_text)!=1:
            raise ValueError('old_text must match exactly once; read the current file and choose a unique block')
        if old_text==new_text:raise ValueError('Patch makes no change. Diagnose the failing behavior and provide a different replacement.')
        content=value['content'].replace(old_text,new_text,1)
        if str(path).endswith(('.py','.pyw')):ast.parse(content,filename=path)
        return self.write(path,content,expected_sha256)

    def invoke(self, name, arguments):
        if name in ASSISTANT_NAMES:return assistant_hub(self).invoke(name,arguments)
        handlers = {"list_files": lambda: {"root": str(self.root), "files": self.files()}, "read_file": self.read,
                    "write_file": self.write, "patch_file":self.patch, "run_command": self.command, "validate_project": self.validate, "run_project": self.run}
        if name not in handlers:
            raise ValueError("Unknown project tool")
        self.event("tool", {"name": name, "arguments": {k: v for k, v in arguments.items() if k not in {'content','old_text','new_text'}}})
        return handlers[name](**arguments)
