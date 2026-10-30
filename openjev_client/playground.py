"""Browser playground: edit a state and questions, see the answers, save them as samples.

    openjev playground [--port 3020] [--samples DIR]

Serves playground.html and a small JSON API on 127.0.0.1 only:
    GET    /api/status              OpenJev /v1/version (or the error)
    GET    /api/samples             every sample
    POST   /api/run                 {state, questions, expect?} -> answers
    POST   /api/samples             save a new sample -> {id}
    PUT    /api/samples/<id>        overwrite a sample
    DELETE /api/samples/<id>
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import Jev, JevError
from . import samples as S

PAGE = Path(__file__).with_name("playground.html")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    jev: Jev
    dir: Path

    def log_message(self, *a):
        pass

    def send_body(self, code: int, data: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code: int, obj):
        self.send_body(code, json.dumps(obj).encode(), "application/json")

    def local(self) -> bool:
        # Refuse other hosts (DNS rebinding) and cross-site writes: the page sends X-Playground, which
        # a foreign page can't without a CORS preflight that this server never answers.
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            self.send_json(403, {"error": "use http://127.0.0.1"}); return False
        if self.command != "GET" and self.headers.get("X-Playground") != "1":
            self.send_json(403, {"error": "missing X-Playground header"}); return False
        return True

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        if not self.local():
            return
        if self.path in ("/", "/index.html"):
            return self.send_body(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/status":
            shown = str(self.dir).replace(str(Path.home()), "~", 1)
            try:
                return self.send_json(200, {"ok": True, "url": self.jev.url, "version": self.jev.version(), "samples": shown})
            except JevError as e:
                return self.send_json(200, {"ok": False, "url": self.jev.url, "error": str(e), "samples": shown})
        if self.path == "/api/samples":
            return self.send_json(200, S.list_samples(self.dir))
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.local():
            return
        try:
            b = self.body()
            if self.path == "/api/run":
                return self.send_json(200, S.run(b, self.jev))
            if self.path == "/api/samples":
                return self.send_json(200, {"id": S.save(b, d=self.dir)})
        except (ValueError, KeyError, TypeError) as e:
            return self.send_json(400, {"error": str(e)})
        except JevError as e:
            return self.send_json(502, {"error": str(e)})
        self.send_json(404, {"error": "not found"})

    def do_PUT(self):
        if not self.local():
            return
        sid = self.path.removeprefix("/api/samples/")
        try:
            self.send_json(200, {"id": S.save(self.body(), sid, d=self.dir)})
        except (ValueError, TypeError) as e:
            self.send_json(400, {"error": str(e)})

    def do_DELETE(self):
        if not self.local():
            return
        try:
            S.delete(self.path.removeprefix("/api/samples/"), self.dir)
            self.send_json(200, {"ok": True})
        except (ValueError, FileNotFoundError) as e:
            self.send_json(404, {"error": str(e)})


def serve(jev: Jev, samples_dir: Path, port: int = 3020, open_browser: bool = True):
    Handler.jev, Handler.dir = jev, samples_dir
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"playground on {url}  (OpenJev: {jev.url}, samples: {samples_dir}); Ctrl-C to stop", flush=True)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
