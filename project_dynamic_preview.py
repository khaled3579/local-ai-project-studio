"""Launch a manifest-declared, owned Python backend with disposable data."""
import argparse
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import quote, urlsplit

import psutil


def configuration(root):
    root = Path(root).resolve()
    manifest = root / 'project-preview.json'
    if manifest.is_symlink() or manifest.stat().st_size > 65536:
        raise ValueError('Invalid backend preview manifest')
    value = json.loads(manifest.read_text(encoding='utf-8'))
    if set(value) != {'version','backend'} or value['version'] != 1:
        raise ValueError('Unsupported preview configuration')
    backend = value['backend']
    if not isinstance(backend,dict) or set(backend) != {'kind','entry','args','ready_prefix'} or backend['kind'] != 'python':
        raise ValueError('Preview supports only an explicitly declared local Python entry')
    entry = backend['entry']
    if not isinstance(entry,str) or not entry or Path(entry).is_absolute():
        raise ValueError('Preview entry must be project-relative')
    target = (root / entry).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix != '.py':
        raise ValueError('Preview entry is not an existing project Python file')
    argv = backend['args']
    if (not isinstance(argv,list) or not 1 <= len(argv) <= 24 or any(not isinstance(a,str) or len(a)>500 or '\x00' in a for a in argv)
            or argv.count('{port}') != 1 or argv.count('{database}') != 1
            or any(('{' in a or '}' in a) and a not in ('{port}','{database}') for a in argv)):
        raise ValueError('Backend arguments require one standalone {port} and {database} placeholder')
    if not isinstance(backend['ready_prefix'],str) or not 1 <= len(backend['ready_prefix']) <= 80:
        raise ValueError('Invalid backend readiness prefix')
    return backend


def owned_listener(process, port):
    owners = {process.pid, *(child.pid for child in psutil.Process(process.pid).children(recursive=True))}
    return any(row.status == psutil.CONN_LISTEN and row.laddr.ip == '127.0.0.1'
               and row.laddr.port == port and row.pid in owners for row in psutil.net_connections(kind='tcp'))


def stop_owned(process):
    try:
        owner = psutil.Process(process.pid)
        children = owner.children(recursive=True)
        for child in reversed(children):
            try: child.terminate()
            except psutil.NoSuchProcess: pass
        try: owner.terminate()
        except psutil.NoSuchProcess: pass
        _, alive = psutil.wait_procs(children + [owner],timeout=3)
        for child in alive:
            try: child.kill()
            except psutil.NoSuchProcess: pass
    except psutil.NoSuchProcess:
        pass


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--entry',required=True)
    parser.add_argument('--watch-parent',required=True,type=int)
    args=parser.parse_args()
    root=Path(args.root).resolve()
    profile=configuration(root)
    html=(root/args.entry).resolve()
    if not html.is_relative_to(root) or not html.is_file() or html.suffix.lower() not in ('.html','.htm'):
        raise ValueError('Choose an existing project HTML page')
    python=root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.is_file():
        raise ValueError('Create the project Python environment before backend preview')
    data=root/'.local-ai'/'tools'/'browser'/('backend-'+uuid.uuid4().hex)
    if not data.resolve().is_relative_to((root/'.local-ai').resolve()):
        raise ValueError('Invalid disposable preview data path')
    data.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
    argv=[str(python),'-u',str(root/profile['entry']),*[str(port) if a=='{port}' else str(data/'store.sqlite3') if a=='{database}' else a for a in profile['args']]]
    child=subprocess.Popen(argv,cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=sys.stderr,
        text=True,encoding='utf-8',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    parent=psutil.Process(args.watch_parent)
    parent_created=parent.create_time()
    lines=queue.Queue(maxsize=256)
    def read():
        for line in child.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=read,daemon=True).start()
    def watch():
        while child.poll() is None:
            time.sleep(1)
            try:
                if parent.create_time()!=parent_created or not parent.is_running():
                    stop_owned(child);os._exit(0)
            except psutil.Error:
                stop_owned(child);os._exit(0)
    threading.Thread(target=watch,daemon=True).start()
    try:
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            line=lines.get(timeout=max(.1,deadline-time.monotonic()))
            if line is None: raise RuntimeError('Backend exited before readiness')
            if not line.startswith(profile['ready_prefix']):
                continue
            url=line[len(profile['ready_prefix']):].strip()
            parsed=urlsplit(url)
            if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.port!=port or parsed.username or parsed.password
                    or not owned_listener(child,port)):
                raise RuntimeError('Readiness URL is not the owned backend listener')
            print('PREVIEW_URL=http://127.0.0.1:'+str(port)+'/'+quote(args.entry.replace('\\','/')),flush=True)
            while child.poll() is None:
                # Drain output continuously; a verbose backend must not block
                # once the one readiness line has been consumed by Studio.
                try: lines.get(timeout=1)
                except queue.Empty: pass
            raise RuntimeError('Owned backend preview exited: '+str(child.returncode))
        raise RuntimeError('Backend readiness timed out')
    finally:
        stop_owned(child)


if __name__=='__main__':
    main()
