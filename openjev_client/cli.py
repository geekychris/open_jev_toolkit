"""openjev: ask the local OpenJev API one question from the shell.

  openjev choose "Which team?" billing shipping technical  < ticket.txt
  openjev check  "Is the customer angry?"                  < ticket.txt
  openjev rate   "How urgent?" "can wait" "this week" "today" "right now" < ticket.txt
  openjev version

The state is read from --state, or stdin. --json prints the raw answer.
"""
import argparse
import json
import sys

from . import Jev, JevError


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
    a = ap.parse_args(argv)

    jev = Jev(a.url)
    try:
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
