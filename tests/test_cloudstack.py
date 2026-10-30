"""Wire-format tests for the cloud_stack adapter (no model needed)."""
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openjev_client import Jev
from openjev_client import cloudstack as cs

CS_REQUEST = {
    "model": "",
    "state": {"command": "rm -rf /tmp/build"},
    "questions": [
        {"id": "risk", "ask": "How risky is this command?", "kind": "choice", "options": ["low", "high"]},
        {"id": "allowed", "ask": "Should it run unattended?", "kind": "bool"},
        {"id": "sev", "ask": "How severe?", "kind": "score", "levels": ["none", "minor", "major"]},
    ],
}
SYSTEMONE_ANSWERS = {
    "risk": {"type": "choice", "choice": "high", "probabilities": {"low": 0.1, "high": 0.9}, "confidence": 0.8},
    "allowed": {"type": "noul", "noul": 0.2},
    "sev": {"type": "score", "score": 1.3, "legend": {"0": "none", "1": "minor", "2": "major"},
            "probabilities": {"0": 0.1, "1": 0.5, "2": 0.4}, "confidence": 0.3},
}


class Translate(unittest.TestCase):
    def test_request(self):
        state, qs = cs.to_systemone(CS_REQUEST)
        self.assertEqual(state, {"command": "rm -rf /tmp/build"})
        self.assertEqual(qs["risk"], {"type": "choice", "instructions": "How risky is this command?", "criteria": {"low": None, "high": None}})
        self.assertEqual(qs["allowed"], {"type": "noul", "instructions": "Should it run unattended?"})
        self.assertEqual(qs["sev"]["criteria"], ["none", "minor", "major"])

    def test_response(self):
        _, qs = cs.to_systemone(CS_REQUEST)
        out = cs.from_systemone(qs, SYSTEMONE_ANSWERS)["answers"]
        self.assertEqual([a["id"] for a in out], ["risk", "allowed", "sev"])
        self.assertEqual(out[0], {"id": "risk", "answer": "high", "confidence": 0.9})
        self.assertEqual(out[1], {"id": "allowed", "answer": False, "confidence": 0.8})
        self.assertEqual(out[2], {"id": "sev", "answer": "minor", "confidence": 0.5})

    def test_response_options(self):
        _, qs = cs.to_systemone(CS_REQUEST)
        out = cs.from_systemone(qs, SYSTEMONE_ANSWERS, confidence="jev", score_as="number")["answers"]
        self.assertEqual(out[0]["confidence"], 0.8)
        self.assertEqual(out[2]["answer"], 1.3)

    def test_bad_requests(self):
        for bad in ({"questions": []}, {"state": {}, "questions": []},
                    {"state": {}, "questions": [{"id": "a", "ask": "x", "kind": "maybe"}]},
                    {"state": {}, "questions": [{"id": "a", "ask": "x", "kind": "choice", "options": ["one"]}]},
                    {"state": {}, "questions": [{"id": "a", "ask": "x", "kind": "bool"}, {"id": "a", "ask": "y", "kind": "bool"}]}):
            with self.assertRaises(cs.BadRequest):
                cs.to_systemone(bad)


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class FakeOpenJev(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        data = json.dumps({"answers": SYSTEMONE_ANSWERS}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.up = serve(FakeOpenJev)
        cs.Handler.jev = Jev(f"http://127.0.0.1:{self.up.server_port}", token="")
        cs.Handler.token = "k"
        self.ad = serve(cs.Handler)
        self.url = f"http://127.0.0.1:{self.ad.server_port}/v1/decide"

    def tearDown(self):
        self.up.shutdown(); self.ad.shutdown()

    def post(self, body, token="k"):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_round_trip(self):
        code, body = self.post(CS_REQUEST)
        self.assertEqual(code, 200)
        self.assertEqual(body["answers"][0]["answer"], "high")

    def test_auth_and_errors(self):
        self.assertEqual(self.post(CS_REQUEST, token="wrong")[0], 401)
        self.assertEqual(self.post({"state": {}, "questions": []})[0], 400)
        self.up.shutdown(); self.up.server_close()
        self.assertEqual(self.post(CS_REQUEST)[0], 502)  # cloud_stack treats >=500 as transient


if __name__ == "__main__":
    unittest.main()
