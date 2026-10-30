"""openjev: ask the local OpenJev API one question from the shell.

  openjev choose "Which team?" billing shipping technical  < ticket.txt
  openjev check  "Is the customer angry?"                  < ticket.txt
  openjev rate   "How urgent?" "can wait" "this week" "today" "right now" < ticket.txt
  openjev version

  openjev samples [--tag build]                 list saved samples
  openjev run [ID ...] [--tag build] [-v]       run samples, check expected answers (exit 1 on a miss)
  openjev playground                            browser UI for trying questions and saving samples

The state is read from --state, or stdin. --json prints the raw answer.
Samples live in $OPENJEV_SAMPLES (default: the toolkit's samples/); --samples overrides.
"""
import argparse
import json
import sys
from pathlib import Path

from . import Jev, JevError
from . import samples as S


def _pick(a) -> list:
    d = Path(a.samples) if a.samples else S.default_dir()
    items = [s for s in S.list_samples(d) if not a.ids or s["id"] in a.ids]
    missing = set(a.ids) - {s["id"] for s in items}
    if missing:
        raise SystemExit(f"openjev: no sample {', '.join(sorted(missing))} in {d}")
    return [s for s in items if not a.tag or a.tag in s.get("tags", [])]


def run_samples(a, jev: Jev) -> int:
    items = _pick(a)
    passed = checked = failed_samples = 0
    for s in items:
        if "error" in s:
            print(f"{s['id']:<34} {s['error']}"); failed_samples += 1; continue
        r = S.run(s, jev)
        passed += r["passed"]; checked += r["checked"]
        bad = r["checked"] - r["passed"]
        failed_samples += bad > 0
        mark = "--  " if not r["checked"] else ("ok  " if not bad else "FAIL")
        parts = []
        for qid, ans in r["answers"].items():
            flag = "" if ans["ok"] is None else (" ok" if ans["ok"] else f" XX want {ans['expected']}")
            parts.append(f"{qid}={S.fmt_answer(ans)}{flag}")
        print(f"{mark} {r['elapsed_ms']:5d} ms  {s['id']:<34} " + ("  ".join(parts) if a.verbose or bad else ""))
    print(f"\n{len(items)} samples, {passed}/{checked} expected answers matched")
    return 1 if failed_samples else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="openjev", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="API base (default $OPENJEV_URL or http://127.0.0.1:3000)")
    ap.add_argument("--state", help="state text (default: read stdin)")
    ap.add_argument("--json", action="store_true", help="print the raw answer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("choose", "rate"):
        p = sub.add_parser(name); p.add_argument("question"); p.add_argument("options", nargs="+")
    sub.add_parser("check").add_argument("question")
    sub.add_parser("version")
    for name in ("samples", "run"):
        p = sub.add_parser(name)
        p.add_argument("ids", nargs="*", metavar="ID", help="sample ids (file names without .json)")
        p.add_argument("--tag"); p.add_argument("--samples", help="samples directory")
        p.add_argument("-v", "--verbose", action="store_true", help="show every answer, not just misses")
    p = sub.add_parser("playground")
    p.add_argument("--port", type=int, default=3020); p.add_argument("--samples", help="samples directory")
    p.add_argument("--no-open", action="store_true", help="don't open a browser")
    a = ap.parse_args(argv)

    jev = Jev(a.url)
    try:
        if a.cmd == "samples":
            for s in _pick(a):
                n = len(s.get("questions", {}))
                print(f"{s['id']:<34} {n} q  {','.join(s.get('tags', [])):<14} {s.get('error') or s.get('name', '')}")
            return 0
        if a.cmd == "run":
            return run_samples(a, jev)
        if a.cmd == "playground":
            from .playground import serve
            serve(jev, Path(a.samples) if a.samples else S.default_dir(), a.port, open_browser=not a.no_open)
            return 0
        if a.cmd == "version":
            print(json.dumps(jev.version(), indent=2)); return 0
        state = a.state if a.state is not None else sys.stdin.read()
        if a.cmd == "choose":
            ans = jev.choose(state, a.question, a.options)
            out = ans.choice
        elif a.cmd == "rate":
            ans = jev.rate(state, a.question, a.options)
            out = f"{ans.score:.2f} ({ans.choice})"
        else:
            p = jev.check(state, a.question)
            ans = None; out = f"{p:.3f}"
        print(json.dumps(ans.raw if ans else {"noul": float(out)}) if a.json else out)
        return 0
    except JevError as e:
        print(f"openjev: {e}", file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main())
