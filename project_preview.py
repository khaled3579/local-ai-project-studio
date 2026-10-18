"""Loopback-only static project preview. Hidden metadata and directory listings are denied."""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote, quote


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--root', required=True)
    args.add_argument('--entry', required=True)
    args.add_argument('--watch-parent', type=int)
    value = args.parse_args()
    root = Path(value.root).resolve()
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)
        def allowed(self):
            path = unquote(urlsplit(self.path).path).replace('\\', '/')
            parts = Path(path.lstrip('/')).parts
            return not any(p.startswith('.') or p in {'node_modules', '__pycache__', 'venv'} for p in parts) and (root / path.lstrip('/')).resolve().is_relative_to(root)
        def do_GET(self):
            if not self.allowed():
                self.send_error(403);return
            super().do_GET()
        def do_HEAD(self):
            if not self.allowed():
                self.send_error(403);return
            super().do_HEAD()
        def list_directory(self, path):
            self.send_error(403);return None
        def end_headers(self):
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            super().end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    if value.watch_parent:
        import threading, time, os, psutil
        owner=psutil.Process(value.watch_parent);created=owner.create_time()
        def watch():
            while True:
                time.sleep(2)
                try:
                    if not owner.is_running() or owner.create_time()!=created:os._exit(0)
                except psutil.Error:os._exit(0)
        threading.Thread(target=watch,daemon=True).start()
    print('PREVIEW_URL=http://127.0.0.1:' + str(server.server_port) + '/' + quote(value.entry.replace('\\', '/')), flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
