"""Concurrency benchmark against a fake /v1/systemone that takes 50 ms and runs requests in parallel."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openjev_client import Jev
from openjev_client import bench

SEEN = []


class Slow(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body["state"])
        time.sleep(0.05)
        out = json.dumps({"answers": {q: {"type": "noul", "noul": 0.9} for q in body["questions"]}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


WORK = [{"state": "text sample", "questions": {"a": {"type": "noul", "instructions": "?"}, "b": {"type": "noul", "instructions": "?"}}},
        {"state": {"log": "x"}, "questions": {"a": {"type": "noul", "instructions": "?"}}}]


class Bench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ThreadingHTTPServer.daemon_threads = True
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.jev = Jev(f"http://127.0.0.1:{cls.srv.server_port}")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def test_throughput_scales_and_report(self):
        res = bench.run(self.jev, WORK, levels=(1, 4), duration=1.0)
        one, four = res
        self.assertEqual((one["concurrency"], four["concurrency"]), (1, 4))
        self.assertGreater(four["req_per_s"], one["req_per_s"] * 2.5)     # parallel server: ~4x
        self.assertGreaterEqual(one["p50_ms"], 50)
        self.assertEqual(one["errors"] + four["errors"], 0)
        self.assertAlmostEqual(one["q_per_s"] / one["req_per_s"], 1.5, delta=0.3)   # 2 and 1 questions alternating
        text = bench.report(res, "fake")
        self.assertIn("peak", text)
        self.assertIn("█", text)

    def test_unique_inputs(self):
        SEEN.clear()
        bench.run(self.jev, WORK, levels=(2,), duration=0.3)
        texts = [s for s in SEEN if isinstance(s, str)]
        dicts = [s for s in SEEN if isinstance(s, dict)]
        self.assertEqual(len(set(texts)), len(texts))                     # every text state is new
        self.assertTrue(all(d["request"].startswith("bench-") and d["log"] == "x" for d in dicts))
        SEEN.clear()
        bench.run(self.jev, WORK, levels=(1,), duration=0.2, unique=False)
        self.assertIn("text sample", SEEN)

    def test_stops_when_failing(self):
        dead = Jev("http://127.0.0.1:9", timeout=0.5)
        with self.assertRaises(Exception):   # the warm-up request fails before any level runs
            bench.run(dead, WORK, levels=(1, 2), duration=0.2)


if __name__ == "__main__":
    unittest.main()
