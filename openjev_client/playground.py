"""Browser playground: edit a state and questions, see the answers, save them as samples.

    openjev playground [--port 3020] [--samples DIR]                 this machine only
    openjev playground --host 0.0.0.0                                the network, with a token
    openjev playground --host 0.0.0.0 --debug                        the network, no token

Serves playground.html and a small JSON API:
    GET    /api/status              OpenJev /v1/version (or the error)
    GET    /api/samples             every sample
    POST   /api/run                 {state, questions, expect?} -> answers
    POST   /api/samples             save a new sample -> {id}
    PUT    /api/samples/<id>        overwrite a sample
    DELETE /api/samples/<id>
    POST   /api/bench               {levels, duration, unique, ids?}: start a concurrency run (one at a time)
    GET    /api/bench               its progress and per-level results
    POST   /api/bench/stop

Access: on 127.0.0.1 no token is needed. On any other address a token is required (given with
--token or $PLAYGROUND_TOKEN, else generated and printed): open /?token=<token> once and a cookie
keeps the browser signed in; scripts can send Authorization: Bearer <token>. --debug turns the token
off. In every mode the server only answers to its own host names and addresses (DNS-rebinding guard;
--allow-host adds more) and writes need the X-Playground header the page sends (CSRF guard).
"""
from __future__ import annotations

import hmac
import json
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlsplit

from . import Jev, JevError
from . import bench
from . import samples as S

PAGE = Path(__file__).with_name("playground.html")
COOKIE = "openjev_playground"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

LOGIN = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>OpenJev Playground</title>
<style>body{font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f6f3;color:#1d1d1b;
display:grid;place-items:center;min-height:100vh;margin:0;padding:16px;box-sizing:border-box}
@media (prefers-color-scheme:dark){body{background:#161615;color:#ecebe6}input{background:#1f1f1d;color:inherit}}
form{display:grid;gap:10px;width:min(420px,100%%)}input,button{font:inherit;padding:8px 10px;border-radius:6px;border:1px solid #888}
button{background:#2f5fd0;color:#fff;border-color:#2f5fd0}.e{color:#b3261e}</style></head><body>
<form method="get" action="/"><h2 style="margin:0">OpenJev Playground</h2>
<label for="t">This playground needs its token (PLAYGROUND_TOKEN on the server).</label>
<input id="t" name="token" type="password" autocomplete="current-password" autofocus required>
%s<button>Open</button></form></body></html>"""


def primary_ip() -> str:
    """The address used for outbound traffic, i.e. the one on the LAN (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def host_names(bind: str, extra: Iterable[str] = ()) -> set:
    """Names and addresses this server may be reached by: loopback, the machine's host names, its
    addresses, the bind address, and any extra names (for example a reverse proxy's)."""
    names = set(LOOPBACK)
    hn = socket.gethostname()
    short = hn.split(".")[0]
    names |= {hn, short, short + ".local", socket.getfqdn()}
    for name in (hn, short + ".local"):
        try:
            names |= {a[4][0] for a in socket.getaddrinfo(name, None)}
        except OSError:
            pass
    names.add(primary_ip())
    if bind not in ("0.0.0.0", "::", ""):
        names.add(bind)
    names |= set(extra)
    return {n.lower() for n in names}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    jev: Jev
    dir: Path
    token = ""                 # empty: no token needed
    mode = "local"             # local | token | debug (shown in the page header)
    hosts: set = set(LOOPBACK)
    # The one concurrency run this server may have going: shared by every page that's open.
    bench_job: dict = {"running": False, "results": [], "live": {}, "levels": [], "error": ""}
    bench_stop = threading.Event()
    bench_lock = threading.Lock()

    def log_message(self, *a):
        pass

    def send_body(self, code: int, data: bytes, ctype: str, headers: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code: int, obj):
        self.send_body(code, json.dumps(obj).encode(), "application/json")

    def path_only(self) -> str:
        return urlsplit(self.path).path

    def presented_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    def allowed(self) -> bool:
        host = self.headers.get("Host", "")
        name = host[1:host.find("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
        if name.lower() not in self.hosts:
            self.send_json(403, {"error": f"unknown Host {name!r}; start the playground with --allow-host {name}"})
            return False
        if self.command != "GET" and self.headers.get("X-Playground") != "1":
            self.send_json(403, {"error": "missing X-Playground header"})
            return False
        if self.token and not hmac.compare_digest(self.presented_token(), self.token):
            if self.command == "GET" and self.path_only() in ("/", "/index.html"):
                self.login()
            else:
                self.send_json(401, {"error": "playground token required"})
            return False
        return True

    def login(self):
        """GET /?token=...: check it, set the cookie and drop the token from the address bar."""
        given = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
        if given and hmac.compare_digest(given, self.token):
            cookie = f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
            return self.send_body(303, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
        err = '<p class="e">That token is not right.</p>' if given else ""
        self.send_body(401, (LOGIN % err).encode(), "text/html; charset=utf-8")

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        if not self.allowed():
            return
        path = self.path_only()
        if path in ("/", "/index.html"):
            return self.send_body(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/status":
            shown = str(self.dir).replace(str(Path.home()), "~", 1)
            base = {"url": self.jev.url, "samples": shown, "auth": self.mode}
            try:
                return self.send_json(200, {"ok": True, "version": self.jev.version(), **base})
            except JevError as e:
                return self.send_json(200, {"ok": False, "error": str(e), **base})
        if path == "/api/samples":
            return self.send_json(200, S.list_samples(self.dir))
        if path == "/api/bench":
            with self.bench_lock:
                job = dict(self.bench_job, live=dict(self.bench_job["live"]), results=list(self.bench_job["results"]))
            return self.send_json(200, job)
        self.send_json(404, {"error": "not found"})

    def start_bench(self, b: dict):
        levels = [int(x) for x in b.get("levels") or bench.DEFAULT_LEVELS]
        if not levels or len(levels) > 12 or any(not 1 <= c <= 64 for c in levels):
            raise ValueError("levels: 1 to 12 concurrency values, each 1..64")
        duration = float(b.get("duration", 10))
        if not 2 <= duration <= 120:
            raise ValueError("duration: 2..120 seconds per level")
        ids = set(b.get("ids") or [])
        work = [s for s in S.list_samples(self.dir) if "error" not in s and (not ids or s["id"] in ids)]
        if not work:
            raise ValueError("no samples to send")
        unique = bool(b.get("unique", True))
        job, stop = self.bench_job, self.bench_stop
        with self.bench_lock:
            if job["running"]:
                return self.send_json(409, {"error": "a run is already going; stop it first"})
            stop.clear()
            job.update(running=True, results=[], live={}, levels=levels, duration=duration, unique=unique,
                       samples=len(work), error="", started=time.time(), finished=None)

        def go():
            try:
                bench.run(self.jev, work, levels, duration, unique, stop=stop, live=job["live"],
                          progress=lambda r: job["results"].append(r))
            except Exception as e:   # surfaced in the page, not swallowed
                job["error"] = str(e)[:300]
            finally:
                job.update(running=False, finished=time.time(), stopped=stop.is_set())

        threading.Thread(target=go, daemon=True).start()
        self.send_json(200, {"ok": True})

    def do_POST(self):
        if not self.allowed():
            return
        path = self.path_only()
        try:
            b = self.body()
            if path == "/api/run":
                return self.send_json(200, S.run(b, self.jev))
            if path == "/api/samples":
                return self.send_json(200, {"id": S.save(b, d=self.dir)})
            if path == "/api/bench":
                return self.start_bench(b)
            if path == "/api/bench/stop":
                self.bench_stop.set()
                return self.send_json(200, {"ok": True})
        except (ValueError, KeyError, TypeError) as e:
            return self.send_json(400, {"error": str(e)})
        except JevError as e:
            return self.send_json(502, {"error": str(e)})
        self.send_json(404, {"error": "not found"})

    def do_PUT(self):
        if not self.allowed():
            return
        sid = self.path_only().removeprefix("/api/samples/")
        try:
            self.send_json(200, {"id": S.save(self.body(), sid, d=self.dir)})
        except (ValueError, TypeError) as e:
            self.send_json(400, {"error": str(e)})

    def do_DELETE(self):
        if not self.allowed():
            return
        try:
            S.delete(self.path_only().removeprefix("/api/samples/"), self.dir)
            self.send_json(200, {"ok": True})
        except (ValueError, FileNotFoundError) as e:
            self.send_json(404, {"error": str(e)})


def serve(jev: Jev, samples_dir: Path, port: int = 3020, open_browser: bool = True, host: str = "127.0.0.1",
          token: str = "", debug: bool = False, allow_hosts: Iterable[str] = ()):
    local = host in LOOPBACK
    if debug:
        token, mode = "", "debug"
    elif local and not token:
        mode = "local"
    else:
        token, mode = token or secrets.token_urlsafe(24), "token"
    Handler.jev, Handler.dir, Handler.token, Handler.mode = jev, samples_dir, token, mode
    Handler.hosts = host_names(host, allow_hosts)
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer((host, port), Handler)
    shown = "127.0.0.1" if local else primary_ip() if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{port}/"
    print(f"playground on {url}  (OpenJev: {jev.url}, samples: {samples_dir}); Ctrl-C to stop", flush=True)
    if mode == "debug":
        print("DEBUG MODE: no token; anyone who can reach this port can run questions and edit samples", flush=True)
    elif mode == "token":
        print(f"token required; open {url}?token={token} once (it sets a cookie)", flush=True)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, [url + (f"?token={token}" if token else "")]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
