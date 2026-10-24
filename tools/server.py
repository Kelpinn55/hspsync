#!/usr/bin/env python3
"""
server.py - server lokal untuk web RAB-HSP.

  python tools/server.py            -> buka http://localhost:8000

Server untuk pengembangan lokal. Menyajikan public/ (sama persis dengan yang di-deploy ke
Vercel), ditambah dua endpoint yang hanya ada secara lokal:
  POST /api/sync    jalankan sync_hsp.py di latar belakang
  GET  /api/status  status + log terakhir sinkronisasi
"""
import json
import subprocess
import sys
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
STATE = {"running": False, "started": None, "finished": None, "exit_code": None, "log": []}
LOCK = threading.Lock()


def run_sync():
    with LOCK:
        STATE.update(running=True, started=datetime.now().isoformat(timespec="seconds"),
                     finished=None, exit_code=None, log=[])
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "tools" / "sync_hsp.py")], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        with LOCK:
            STATE["log"].append(line.rstrip())
            STATE["log"] = STATE["log"][-200:]
    code = proc.wait()
    with LOCK:
        STATE.update(running=False, exit_code=code, finished=datetime.now().isoformat(timespec="seconds"))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/api/status":
            with LOCK:
                return self._json(STATE)
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path == "/api/sync":
            with LOCK:
                if STATE["running"]:
                    return self._json({"ok": False, "pesan": "Sinkronisasi sedang berjalan"}, 409)
            threading.Thread(target=run_sync, daemon=True).start()
            return self._json({"ok": True})
        self._json({"ok": False}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"RAB-HSP berjalan di http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
