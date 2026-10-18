"""Validate project ZIPs and publish a fresh independent extraction atomically."""
import io
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time
import unicodedata
import zipfile

ZIP_MAX_FILE = 32 * 1024 * 1024
ZIP_MAX_UNPACKED = 200 * 1024 * 1024
ZIP_MAX_ENTRIES = 5000
SKIP_PARTS = {".local-ai", ".venv", "venv", ".git", "node_modules", "__pycache__", ".pytest_cache", "__macosx"}
RESERVED = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$", *[f"com{i}" for i in range(10)], *[f"lpt{i}" for i in range(10)]}


def member_path(info):
    name = info.orig_filename.replace("\\", "/")
    if not name or "\x00" in name or name.startswith("/") or re.search(r"[\x00-\x1f<>:\"|?*]", name):
        raise ValueError("ZIP يحتوي مسارًا غير صالح أو مطلقًا: " + repr(name[:100]))
    raw = name.rstrip("/").split("/")
    if len(raw) > 25 or any(part in {"", ".", ".."} for part in raw):
        raise ValueError("ZIP يحتوي مسارًا خارج مجلد المشروع أو عميقًا جدًا")
    parts = [unicodedata.normalize("NFC", part) for part in raw]
    if any(part.endswith((" ", ".")) or part.split(".")[0].casefold() in RESERVED for part in parts):
        raise ValueError("اسم ملف في ZIP غير صالح على Windows: " + name[:100])
    path = PurePosixPath(*parts)
    if len(str(path)) > 220 or any(len(p) > 120 for p in parts):
        raise ValueError("مسار ملف ZIP طويل جدًا")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR} or stat.S_ISLNK(mode):
        raise ValueError("ZIP يحتوي روابط رمزية أو ملفات خاصة غير مدعومة")
    if info.flag_bits & 1:
        raise ValueError("ZIP محمي بكلمة مرور؛ أرفق نسخة مفتوحة")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError("ZIP يستخدم ضغطًا غير مدعوم؛ استخدم Store أو Deflate")
    return path


def inspect_zip(data):
    if not data or len(data) > ZIP_MAX_FILE:
        raise ValueError("ملف ZIP لا يتجاوز 32MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError("ملف ZIP تالف أو ليس أرشيف ZIP صالحًا") from error
    with archive:
        entries = archive.infolist()
        if len(entries) > ZIP_MAX_ENTRIES:
            raise ValueError("ZIP يتجاوز حد 5000 عنصر")
        files, skipped, seen = [], [], {}
        total = 0
        for info in entries:
            path = member_path(info)
            key = str(path).casefold()
            if key in seen and not (info.is_dir() and seen[key]):
                raise ValueError("ZIP يحتوي أسماء ملفات متعارضة أو مكررة على Windows")
            seen[key] = info.is_dir()
            if info.file_size > ZIP_MAX_FILE:
                raise ValueError("أحد ملفات ZIP يتجاوز 32MB بعد فك الضغط")
            total += info.file_size
            if total > ZIP_MAX_UNPACKED or (info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 200):
                raise ValueError("حجم ZIP بعد فك الضغط أو نسبة ضغطه تتجاوز الحد")
            if any(part.casefold() in SKIP_PARTS for part in path.parts) or path.name.casefold() == ".ds_store":
                if not info.is_dir():
                    skipped.append(str(path))
                continue
            if not info.is_dir():
                files.append((info.filename, path, info.file_size, info.CRC))
        for key in seen:
            parents = PurePosixPath(key).parents
            if any(str(parent) in seen and not seen[str(parent)] for parent in parents):
                raise ValueError("ZIP يحتوي ملفًا ومجلدًا بالمسار نفسه")
        if not files:
            raise ValueError("ZIP لا يحتوي ملفات مشروع قابلة للاستيراد")
        prefix = files[0][1].parts[0] if all(len(p.parts) > 1 and p.parts[0] == files[0][1].parts[0] for _, p, _, _ in files) else ""
        plan = [{"member": member, "path": str(PurePosixPath(*path.parts[1:]) if prefix else path), "bytes": size, "crc": crc} for member, path, size, crc in files]
        return {"file_count": len(plan), "unpacked_bytes": sum(p["bytes"] for p in plan), "root_prefix": prefix,
                "skipped_count": len(skipped), "skipped": skipped, "files": plan}


def extract_zip(data, destination, current_root):
    report = inspect_zip(data)  # Validate the entire directory before creating anything.
    requested = Path(destination).expanduser()
    if not requested.is_absolute():
        raise ValueError("أدخل مسارًا مطلقًا لمجلد الاستيراد الجديد")
    target = requested.resolve()
    current = Path(current_root).resolve()
    if target.exists():
        raise FileExistsError("مجلد الوجهة موجود. اختر مجلدًا جديدًا؛ لن تُستبدل الملفات")
    if target.is_relative_to(current) or current.is_relative_to(target) or target == Path(target.anchor):
        raise ValueError("اختر مجلدًا مستقلًا خارج المشروع الحالي")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".zip-import-", dir=target.parent)).resolve()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in report["files"]:
                output = (stage / entry["path"]).resolve()
                if not output.is_relative_to(stage):
                    raise ValueError("مسار الاستخراج خارج المجلد المستقل")
                output.parent.mkdir(parents=True, exist_ok=True)
                actual = 0
                with archive.open(entry["member"]) as source, output.open("xb") as sink:
                    while chunk := source.read(64 * 1024):
                        actual += len(chunk)
                        if actual > entry["bytes"] or actual > ZIP_MAX_FILE:
                            raise ValueError("حجم بيانات ZIP لا يطابق الفهرس")
                        sink.write(chunk)
                if actual != entry["bytes"]:
                    raise ValueError("استخراج ZIP غير مكتمل")
        # rename refuses an existing destination on Windows; never replace a folder.
        for attempt in range(6):
            if target.exists():
                raise FileExistsError("ظهر مجلد الوجهة أثناء الاستيراد؛ اختر اسمًا جديدًا")
            try:
                stage.rename(target)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
        return {**report, "destination": str(target)}
    except (zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError("تعذّر فك ZIP؛ تحقق من سلامة الأرشيف وCRC") from error
    finally:
        # Only remove our own temporary sibling, after verifying its resolved scope.
        if stage.exists() and stage.parent == target.parent.resolve() and stage.name.startswith(".zip-import-"):
            shutil.rmtree(stage)
