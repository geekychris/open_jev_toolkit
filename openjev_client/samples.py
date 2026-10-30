"""Saved test cases ("samples"): a state, questions, and optionally the answers you expect.

Each sample is one JSON file in a samples directory ($OPENJEV_SAMPLES, else the toolkit's samples/):

    {
      "name": "Ticket: double charge",
      "tags": ["support"],
      "notes": "",
      "state": "I was charged twice and nobody replied.",       # text, or any JSON value
      "questions": {
        "route":   {"type": "choice", "instructions": "Which team?", "criteria": {"billing": null, "shipping": null}},
        "angry":   {"type": "noul",   "instructions": "Is the customer angry?"},
        "urgency": {"type": "score",  "instructions": "How urgent?", "criteria": ["can wait", "today", "right now"]}
      },
      "expect": {"route": "billing", "angry": true, "urgency": ["today", "right now"]}
    }

`questions` is the /v1/systemone wire format, so a sample is also a ready-made request body.
`expect` is optional, per question: a label (choice), the most likely level (score), true/false
(yes/no), or a list of acceptable labels.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from . import Answer, Jev, _parse

TYPES = ("choice", "noul", "score")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def default_dir() -> Path:
    if os.environ.get("OPENJEV_SAMPLES"):
        return Path(os.environ["OPENJEV_SAMPLES"]).expanduser()
    toolkit = Path(__file__).resolve().parent.parent / "samples"
    return toolkit if toolkit.is_dir() else Path.cwd() / "samples"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return s or "sample"


def _path(d: Path, sid: str) -> Path:
    if not _ID.match(sid):
        raise ValueError(f"bad sample id {sid!r}")
    return d / f"{sid}.json"


def validate(s: Any) -> dict:
    """Check a sample's shape; returns it. Raises ValueError with a readable message."""
    if not isinstance(s, dict):
        raise ValueError("a sample is a JSON object")
    if not str(s.get("name", "")).strip():
        raise ValueError("name is required")
    if "state" not in s or s["state"] in ("", None):
        raise ValueError("state is required")
    qs = s.get("questions")
    if not isinstance(qs, dict) or not qs:
        raise ValueError("questions must be a non-empty object {id: question}")
    for qid, q in qs.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", qid):
            raise ValueError(f"question id {qid!r}: use letters, digits and _")
        if not isinstance(q, dict) or q.get("type") not in TYPES:
            raise ValueError(f"question {qid!r}: type must be one of {', '.join(TYPES)}")
        if not str(q.get("instructions", "")).strip():
            raise ValueError(f"question {qid!r}: instructions are required")
        c = q.get("criteria")
        if q["type"] == "choice" and (not isinstance(c, dict) or len(c) < 2):
            raise ValueError(f"question {qid!r}: a choice needs at least two options")
        if q["type"] == "score" and (not isinstance(c, list) or len(c) < 2):
            raise ValueError(f"question {qid!r}: a score needs at least two levels")
    for qid in (s.get("expect") or {}):
        if qid not in qs:
            raise ValueError(f"expect names unknown question {qid!r}")
    return s


def list_samples(d: Optional[Path] = None) -> list[dict]:
    d = d or default_dir()
    out = []
    for p in sorted(d.glob("*.json"), key=lambda p: p.stem):
        try:
            s = json.loads(p.read_text())
        except ValueError as e:
            s = {"name": p.stem, "error": f"invalid JSON: {e}"}
        s["id"] = p.stem
        out.append(s)
    return out


def load(sid: str, d: Optional[Path] = None) -> dict:
    s = json.loads(_path(d or default_dir(), sid).read_text())
    s["id"] = sid
    return s


def save(sample: dict, sid: Optional[str] = None, d: Optional[Path] = None) -> str:
    """Write a sample; returns its id. Without `sid`, a new id is made from the name."""
    d = d or default_dir()
    s = validate({k: v for k, v in sample.items() if k != "id"})
    if not sid:
        base = sid = slugify(s["name"])
        n = 2
        while _path(d, sid).exists():
            sid = f"{base}-{n}"; n += 1
    p = _path(d, sid)
    d.mkdir(parents=True, exist_ok=True)
    order = ["name", "tags", "notes", "state", "questions", "expect"]
    s = {k: s[k] for k in order if s.get(k) not in (None, "", [], {})} | {k: v for k, v in s.items() if k not in order}
    p.write_text(json.dumps(s, indent=2, ensure_ascii=False) + "\n")
    return sid


def delete(sid: str, d: Optional[Path] = None) -> None:
    _path(d or default_dir(), sid).unlink()


def summary(q: dict, a: Answer) -> dict:
    """A display-friendly answer: score probabilities keyed by level name."""
    out: dict[str, Any] = {"type": a.type, "choice": a.choice, "confidence": a.confidence, "p": a.p, "score": a.score}
    if a.type == "score":
        legend = a.raw.get("legend") or {str(i): lv for i, lv in enumerate(q.get("criteria", []))}
        out["probabilities"] = {legend.get(k, k): v for k, v in a.probabilities.items()}
    else:
        out["probabilities"] = a.probabilities
    return out


def grade(q: dict, a: Answer, expected: Any) -> Optional[bool]:
    """True/False against `expected`, or None when nothing is expected."""
    if expected is None:
        return None
    if q["type"] == "noul":
        return a.p is not None and (a.p >= 0.5) == bool(expected)
    ok = expected if isinstance(expected, list) else [expected]
    return a.choice in ok


def run(sample: dict, jev: Optional[Jev] = None) -> dict:
    """Ask a sample's questions; returns answers, per-question checks and timing."""
    jev = jev or Jev()
    qs = sample["questions"]
    r = jev.ask(sample["state"], **qs)
    expect = sample.get("expect") or {}
    answers = {}
    for qid, q in qs.items():
        a = r.get(qid) or _parse({"type": q["type"]})
        answers[qid] = summary(q, a) | {"expected": expect.get(qid), "ok": grade(q, a, expect.get(qid))}
    checks = [v["ok"] for v in answers.values() if v["ok"] is not None]
    return {"answers": answers, "elapsed_ms": round(r.elapsed_ms), "model": r.model,
            "input_tokens": r.input_tokens, "passed": sum(checks), "checked": len(checks)}


def fmt_answer(a: dict) -> str:
    if a["type"] == "noul":
        return f"p={a['p']:.2f}" if a["p"] is not None else "?"
    if a["type"] == "score":
        return f"{a['choice']} ({a['score']:.2f})" if a["score"] is not None else "?"
    return f"{a['choice']} ({a['confidence']:.2f})" if a["confidence"] is not None else str(a["choice"])
