"""cloud_stack-compatible endpoint in front of the local OpenJev API.

cloud_stack's `jev` agent backing speaks its own wire format. This adapter accepts that format and
translates it to OpenJev's /v1/systemone, so cloud_stack works unchanged: set
JEV_ENDPOINT=http://<host>:3010/v1/decide.

  cloud_stack request                         OpenJev question
  {"model": ..., "state": <any JSON>,         state passed through
   "questions": [
     {"id", "ask", "kind": "choice", "options"}  -> {"type": "choice", "criteria": {opt: null}}
     {"id", "ask", "kind": "score",  "levels"}   -> {"type": "score",  "criteria": [levels]}
     {"id", "ask", "kind": "bool"}               -> {"type": "noul"}
   ]}

  cloud_stack response: {"answers": [{"id", "answer", "confidence"}]}
    choice  answer = chosen option;          confidence = its probability
    score   answer = most likely level name; confidence = its probability
            (ADAPTER_SCORE=number: answer = expected level index instead)
    bool    answer = p(yes) >= 0.5;          confidence = max(p, 1 - p)

  "confidence" is the probability of the returned answer, so cloud_stack's minConfidence reads as
  "at least this likely". ADAPTER_CONFIDENCE=jev returns OpenJev's normalised confidence for
  choice/score instead (0 = uniform, 1 = certain).

Errors map onto cloud_stack's classes: a bad question is HTTP 400 (not retried), an unreachable or
failing OpenJev is HTTP 502 (cloud_stack retries it as transient), and a wrong token is 401.

Run:  python -m openjev_client.cloudstack [--host 0.0.0.0] [--port 3010]
Env:  OPENJEV_URL (upstream, default http://127.0.0.1:3000), ADAPTER_TOKEN (require this bearer
      token; cloud_stack sends JEV_API_KEY), SHIM_TOKEN (sent upstream if the OpenJev API needs one).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import Jev, JevError

KINDS = {"choice", "score", "bool"}


class BadRequest(ValueError):
    pass


def to_systemone(req: dict) -> tuple[object, dict]:
    """cloud_stack request -> (state, systemone questions map)."""
    if not isinstance(req, dict):
        raise BadRequest("body must be a JSON object")
    if "state" not in req:
        raise BadRequest("state is required")
    qs = req.get("questions")
    if not isinstance(qs, list) or not qs:
        raise BadRequest("questions must be a non-empty list")
    out = {}
    for i, q in enumerate(qs):
        qid, ask, kind = q.get("id"), q.get("ask"), q.get("kind")
        if not qid or not isinstance(qid, str):
            raise BadRequest(f"question {i}: id is required")
        if qid in out:
            raise BadRequest(f"question {i}: duplicate id {qid!r}")
        if not ask or not isinstance(ask, str):
            raise BadRequest(f"question {qid}: ask is required")
        if kind not in KINDS:
            raise BadRequest(f"question {qid}: kind must be choice, score or bool, not {kind!r}")
        if kind == "choice":
            opts = q.get("options") or []
            if len(opts) < 2:
                raise BadRequest(f"question {qid}: a choice needs at least two options")
            out[qid] = {"type": "choice", "instructions": ask, "criteria": {str(o): None for o in opts}}
        elif kind == "score":
            levels = q.get("levels") or []
            if len(levels) < 2:
                raise BadRequest(f"question {qid}: a score needs at least two levels")
            out[qid] = {"type": "score", "instructions": ask, "criteria": [str(l) for l in levels]}
        else:
            out[qid] = {"type": "noul", "instructions": ask}
    state = req["state"]
    return (state if isinstance(state, (dict, str)) else json.dumps(state)), out


def from_systemone(qs: dict, answers: dict, confidence: str = "probability", score_as: str = "level") -> dict:
    """systemone answers -> cloud_stack response, in the request's question order."""
    out = []
    for qid, q in qs.items():
        a = answers.get(qid)
        if a is None:
            raise JevError(f"OpenJev did not answer {qid!r}")
        if q["type"] == "noul":
            p = a["noul"]
            out.append({"id": qid, "answer": p >= 0.5, "confidence": round(max(p, 1 - p), 4)})
        elif q["type"] == "choice":
            c = a["choice"]
            conf = a["confidence"] if confidence == "jev" else a["probabilities"][c]
            out.append({"id": qid, "answer": c, "confidence": conf})
        else:
            probs = a["probabilities"]
            top = max(probs, key=probs.get)
            conf = a["confidence"] if confidence == "jev" else probs[top]
            ans = a["score"] if score_as == "number" else a["legend"][top]
            out.append({"id": qid, "answer": ans, "confidence": conf})
    return {"answers": out}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    jev: Jev
    token = ""
    confidence = "probability"
    score_as = "level"

    def log_message(self, *a):
        pass

    def send_json(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorised(self) -> bool:
        if self.token and self.headers.get("Authorization", "") != "Bearer " + self.token:
            self.send_json(401, {"error": "missing or invalid bearer token"})
            return False
        return True

    def do_GET(self):
        if not self.authorised():
            return
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            try:
                return self.send_json(200, {"ok": True, "upstream": self.jev.version()})
            except JevError as e:
                return self.send_json(502, {"ok": False, "error": str(e)})
        self.send_json(404, {"error": "POST /v1/decide, GET /health"})

    def do_POST(self):
        if not self.authorised():
            return
        t0 = time.perf_counter()
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            state, qs = to_systemone(req)
        except (ValueError, AttributeError) as e:
            return self.send_json(400, {"error": str(e)})
        try:
            raw = self.jev._call("POST", "/v1/systemone", {"model": "openjev", "state": state, "questions": qs})
            resp = from_systemone(qs, raw.get("answers", {}), self.confidence, self.score_as)
        except JevError as e:
            code = 400 if str(e).startswith("422") else 502
            return self.send_json(code, {"error": f"OpenJev: {e}"})
        self.send_json(200, resp)
        print(json.dumps({"questions": len(qs), "ms": round((time.perf_counter() - t0) * 1000),
                          "answers": {a["id"]: a["answer"] for a in resp["answers"]}}), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="cloud_stack jev endpoint over local OpenJev")
    ap.add_argument("--host", default=os.environ.get("ADAPTER_HOST", "127.0.0.1"),
                    help="0.0.0.0 so containers can reach it (set ADAPTER_TOKEN)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ADAPTER_PORT", "3010")))
    a = ap.parse_args(argv)
    Handler.jev = Jev()
    Handler.token = os.environ.get("ADAPTER_TOKEN", "")
    Handler.confidence = os.environ.get("ADAPTER_CONFIDENCE", "probability")
    Handler.score_as = os.environ.get("ADAPTER_SCORE", "level")
    if a.host not in ("127.0.0.1", "localhost") and not Handler.token:
        print("warning: listening beyond localhost without ADAPTER_TOKEN", flush=True)
    print(f"cloud_stack adapter on http://{a.host}:{a.port}/v1/decide -> {Handler.jev.url}", flush=True)
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
