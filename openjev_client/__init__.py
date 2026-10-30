"""Client for a local OpenJev decision API (POST /v1/systemone).

OpenJev answers typed questions about a "state" (text, or a dict that is sent as JSON):

    choice  pick one of up to 52 labelled options (more are handled in passes)
    yesno   probability that a statement is true            (wire type "noul")
    score   expected position on an ordered list of levels

    from openjev_client import Jev, choice, yesno, score

    jev = Jev()                                   # $OPENJEV_URL or http://127.0.0.1:3000
    r = jev.ask("Customer: I was charged twice and nobody replied.",
                route=choice("Which team should handle this?", ["billing", "shipping", "technical"]),
                angry=yesno("Is the customer angry?"),
                urgency=score("How urgent is this?", ["can wait", "this week", "today", "right now"]))
    r["route"].choice, r["route"].confidence, r["angry"].p, r["urgency"].score

Standard library only, so it can be dropped into any Python 3.9+ project.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Union

__all__ = ["Jev", "JevError", "Answer", "Result", "choice", "yesno", "score"]

Options = Union[Sequence[str], Mapping[str, Optional[str]]]


def choice(instructions: str, options: Options) -> dict:
    """A multiple-choice question. `options` is a list of labels, or {label: description or None}.
    Descriptions help when a label alone is ambiguous."""
    crit = dict(options) if isinstance(options, Mapping) else {o: None for o in options}
    return {"type": "choice", "instructions": instructions, "criteria": crit}


def yesno(instructions: str, true: Optional[str] = None, false: Optional[str] = None) -> dict:
    """A yes/no question; the answer is a calibrated probability of yes. `true`/`false` optionally
    describe what each side means."""
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {"true": true, "false": false}
    return q


def score(instructions: str, levels: Sequence[str]) -> dict:
    """An ordinal rating. `levels` are ordered lowest first; the answer's `score` is the expected
    level index (0 .. len(levels)-1)."""
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


@dataclass
class Answer:
    type: str
    raw: dict
    choice: Optional[str] = None        # choice: the winning label; score: the most likely level
    probabilities: dict = field(default_factory=dict)
    confidence: Optional[float] = None  # 0 = uniform, 1 = certain (choice/score only)
    p: Optional[float] = None           # yesno: probability of yes
    score: Optional[float] = None       # score: expected level index

    @property
    def yes(self) -> bool:
        return bool(self.p is not None and self.p >= 0.5)

    def __repr__(self) -> str:
        if self.type == "noul":
            return f"Answer(yesno p={self.p:.3f})"
        if self.type == "score":
            return f"Answer(score={self.score:.2f} level={self.choice!r} conf={self.confidence})"
        return f"Answer(choice={self.choice!r} conf={self.confidence})"


class Result(dict):
    """{question_id: Answer}, plus request metadata."""
    model: str = ""
    input_tokens: int = 0
    elapsed_ms: float = 0.0


class JevError(RuntimeError):
    pass


def _parse(raw: dict) -> Answer:
    t = raw.get("type")
    if t == "noul":
        return Answer(type=t, raw=raw, p=raw.get("noul"))
    if t == "score":
        probs = raw.get("probabilities", {})
        legend = raw.get("legend", {})
        top = max(probs, key=probs.get) if probs else None
        return Answer(type=t, raw=raw, score=raw.get("score"), probabilities=probs,
                      confidence=raw.get("confidence"), choice=legend.get(top, top))
    return Answer(type=t or "choice", raw=raw, choice=raw.get("choice"),
                  probabilities=raw.get("probabilities", {}), confidence=raw.get("confidence"))


class Jev:
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, timeout: float = 120.0):
        self.url = (url or os.environ.get("OPENJEV_URL") or "http://127.0.0.1:3000").rstrip("/")
        self.token = token if token is not None else os.environ.get("SHIM_TOKEN", "")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise JevError(f"{e.code}: {e.read().decode(errors='replace')[:300]}") from None
        except urllib.error.URLError as e:
            raise JevError(f"cannot reach {self.url} ({e.reason}); is scripts/serve.sh start running?") from None

    def version(self) -> dict:
        return self._call("GET", "/v1/version")

    def ask(self, state: Union[str, Mapping[str, Any]], **questions: dict) -> Result:
        """Ask several questions about one state in a single request."""
        if not questions:
            raise ValueError("ask() needs at least one question, e.g. route=choice(...)")
        t0 = time.perf_counter()
        out = self._call("POST", "/v1/systemone", {"model": "openjev", "state": state, "questions": questions})
        res = Result({qid: _parse(a) for qid, a in out.get("answers", {}).items()})
        res.model = out.get("model", "")
        res.input_tokens = out.get("usage", {}).get("input_tokens", 0)
        res.elapsed_ms = (time.perf_counter() - t0) * 1000
        return res

    # One-question conveniences
    def choose(self, state, instructions: str, options: Options) -> Answer:
        return self.ask(state, q=choice(instructions, options))["q"]

    def check(self, state, instructions: str, true: Optional[str] = None, false: Optional[str] = None) -> float:
        return self.ask(state, q=yesno(instructions, true, false))["q"].p

    def rate(self, state, instructions: str, levels: Sequence[str]) -> Answer:
        return self.ask(state, q=score(instructions, levels))["q"]
