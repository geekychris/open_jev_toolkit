"""Build-pipeline decisions: classify a failed verify step from its log tail and choose what the
agent loop does next. A preview of the cloud_stack use cases (see README "Next: agentic builds").

Each sample has an expected label, so this doubles as a tiny eval: the pattern to use before trusting
a question in a pipeline is to collect real log tails, label them, and measure.
"""
from openjev_client import Jev, choice, yesno

FAILURE = {
    "compile": "The code does not compile or type-check (syntax error, undefined symbol, type mismatch).",
    "test_assertion": "Code builds, but a test ran and its assertion failed.",
    "lint": "A linter, formatter or vet check rejected the code.",
    "dependency": "A package or module could not be resolved, downloaded or is the wrong version.",
    "environment": "Toolchain, permissions, disk, missing binary or misconfigured machine; not the code.",
    "flaky_or_network": "Timeout, connection reset, rate limit or a test that fails intermittently.",
}
NEXT = {
    "retry": "Run the same step again unchanged; the failure is likely transient.",
    "fix_code": "Send the error back to the coding agent to change the code.",
    "fix_environment": "Repair the workspace or toolchain, then rerun.",
    "escalate": "Stop and ask a human; the agent is unlikely to fix this alone.",
}

SAMPLES = [
    ("compile", "fix_code", """$ go build ./...
# github.com/acme/orders/internal/api
internal/api/handlers.go:88:14: undefined: OrderStatusPending
internal/api/handlers.go:91:9: cannot use resp (variable of type *Order) as OrderDTO value in return statement"""),
    ("test_assertion", "fix_code", """$ go test ./internal/pricing/...
--- FAIL: TestDiscountStacking (0.00s)
    pricing_test.go:57: expected total 81.00, got 72.90
FAIL
FAIL	github.com/acme/orders/internal/pricing	0.214s"""),
    ("lint", "fix_code", """$ golangci-lint run
internal/api/routes.go:12:2: `fmt` imported and not used (unused)
internal/api/routes.go:40:1: exported function NewRouter should have comment or be unexported (revive)"""),
    ("dependency", "fix_environment", """$ npm ci
npm ERR! code ERESOLVE
npm ERR! ERESOLVE unable to resolve dependency tree
npm ERR! peer react@"^17.0.0" from react-beautiful-dnd@13.1.1
npm ERR! Found: react@18.3.1"""),
    ("environment", "fix_environment", """$ ./mvnw -q verify
/bin/sh: 1: ./mvnw: Permission denied
Error: Process completed with exit code 126."""),
    ("flaky_or_network", "retry", """$ go mod download
go: github.com/stretchr/testify@v1.9.0: Get "https://proxy.golang.org/github.com/stretchr/testify/@v/v1.9.0.zip":
dial tcp 142.250.72.17:443: i/o timeout"""),
    ("test_assertion", "escalate", """$ go test ./...
--- FAIL: TestLedgerReconciliation (0.31s)
    ledger_test.go:204: balance mismatch for account 9917: expected 0, got -0.01
    (attempt 3 of 3 by agent: previous fixes changed rounding mode, then the test fixture, then reverted)
FAIL"""),
]

jev = Jev()
ok_kind = ok_next = 0
for want_kind, want_next, log in SAMPLES:
    state = {"step": "verify", "attempt_history": "see log", "log_tail": log}
    r = jev.ask(state,
                kind=choice("What kind of failure is this?", FAILURE),
                next=choice("What should the build agent do next?", NEXT),
                code_change=yesno("Was this failure caused by the code change under test, rather than the environment?"))
    k, n = r["kind"].choice, r["next"].choice
    ok_kind += k == want_kind; ok_next += n == want_next
    print(f"{r.elapsed_ms:5.0f} ms  kind={k:<17}{'ok ' if k == want_kind else 'XX '} next={n:<16}{'ok ' if n == want_next else 'XX '}"
          f"conf={r['next'].confidence:.2f} code_change={r['code_change'].p:.2f}  | {log.splitlines()[0]}")
print(f"\nkind {ok_kind}/{len(SAMPLES)}   next {ok_next}/{len(SAMPLES)}")
