"""Samples store, grading and the playground API (no model needed: a fake /v1/systemone)."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openjev_client import Jev
from openjev_client import playground
from openjev_client import samples as S

SAMPLE = {
    "name": "Ticket: double charge",
    "tags": ["support"],
    "state": "I was charged twice.",
    "questions": {
        "route": {"type": "choice", "instructions": "Which team?", "criteria": {"billing": None, "shipping": None}},
        "angry": {"type": "noul", "instructions": "Angry?"},
        "urgency": {"type": "score", "instructions": "How urgent?", "criteria": ["low", "high"]},
    },
    "expect": {"route": "billing", "angry": False, "urgency": ["low", "high"]},
}
ANSWERS = {
    "route": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.9, "shipping": 0.1}, "confidence": 0.7},
    "angry": {"type": "noul", "noul": 0.8},
    "urgency": {"type": "score", "score": 0.7, "legend": {"0": "low", "1": "high"}, "probabilities": {"0": 0.3, "1": 0.7}, "confidence": 0.2},
}


class FakeJev(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self._send({"model": "fake"})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self._send({"model": "fake", "answers": ANSWERS, "usage": {"input_tokens": 12}})

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_dedupe(self):
        a = S.save(SAMPLE, d=self.d)
        b = S.save(SAMPLE, d=self.d)
        self.assertEqual((a, b), ("ticket-double-charge", "ticket-double-charge-2"))
        self.assertEqual(S.load(a, self.d)["questions"], SAMPLE["questions"])
        self.assertEqual([s["id"] for s in S.list_samples(self.d)], [a, b])
        S.save(dict(SAMPLE, name="renamed"), a, d=self.d)
        self.assertEqual(S.load(a, self.d)["name"], "renamed")
        S.delete(b, self.d)
        self.assertEqual(len(S.list_samples(self.d)), 1)

    def test_rejects(self):
        for bad, msg in [({**SAMPLE, "questions": {}}, "questions"),
                         ({**SAMPLE, "questions": {"x": {"type": "choice", "instructions": "?", "criteria": {"a": None}}}, "expect": {}}, "two options"),
                         ({**SAMPLE, "expect": {"nope": "a"}}, "unknown question"),
                         ({**SAMPLE, "state": ""}, "state")]:
            with self.assertRaisesRegex(ValueError, msg):
                S.save(bad, d=self.d)
        with self.assertRaisesRegex(ValueError, "bad sample id"):
            S.save(SAMPLE, "../escape", d=self.d)

    def test_broken_file_listed(self):
        (self.d / "broken.json").write_text("{nope")
        self.assertIn("error", S.list_samples(self.d)[0])


class Run(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, url = start(FakeJev)
        cls.jev = Jev(url)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def test_run_grades(self):
        r = S.run(SAMPLE, self.jev)
        self.assertEqual((r["passed"], r["checked"]), (2, 3))
        self.assertTrue(r["answers"]["route"]["ok"])
        self.assertFalse(r["answers"]["angry"]["ok"])          # p=0.8 but expected False
        self.assertTrue(r["answers"]["urgency"]["ok"])         # list of acceptable levels
        self.assertEqual(r["answers"]["urgency"]["probabilities"], {"low": 0.3, "high": 0.7})


class Playground(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake, url = start(FakeJev)
        cls.tmp = tempfile.TemporaryDirectory()
        playground.Handler.jev, playground.Handler.dir = Jev(url), Path(cls.tmp.name)
        cls.srv, cls.url = start(playground.Handler)

    @classmethod
    def tearDownClass(cls):
        for s in (cls.srv, cls.fake): s.shutdown(); s.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json", "X-Playground": "1"} if headers is None else headers
        req = urllib.request.Request(self.url + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_page_and_crud(self):
        code, page = self.call("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"OpenJev Playground", page)
        code, out = self.call("POST", "/api/samples", SAMPLE)
        sid = json.loads(out)["id"]
        code, out = self.call("POST", "/api/run", SAMPLE)
        self.assertEqual(json.loads(out)["passed"], 2)
        self.assertEqual(self.call("PUT", f"/api/samples/{sid}", dict(SAMPLE, notes="n"))[0], 200)
        self.assertEqual(json.loads(self.call("GET", "/api/samples")[1])[0]["notes"], "n")
        self.assertEqual(self.call("DELETE", f"/api/samples/{sid}")[0], 200)

    def test_guards(self):
        self.assertEqual(self.call("POST", "/api/samples", SAMPLE, headers={"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.call("GET", "/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/samples", {"name": "x"})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/samples/..%2Fx")[0], 404)


if __name__ == "__main__":
    unittest.main()
