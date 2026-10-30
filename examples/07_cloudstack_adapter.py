"""Call the cloud_stack-compatible endpoint exactly as cloud_stack's jev backing does.
Start it first: scripts/serve.sh start && scripts/cloudstack-adapter.sh start"""
import json
import os
import urllib.request

URL = os.environ.get("JEV_ENDPOINT", "http://127.0.0.1:3010/v1/decide")
profile = json.load(open(os.path.join(os.path.dirname(__file__), "../integrations/cloudstack/verify-triage.agent.json")))

# What JevBacking.Invoke sends: the node's inputs as state, one wire question per configured question (id = its "into").
body = {
    "state": {
        "step": "verify",
        "attempt": 3,
        "log_tail": "--- FAIL: TestLedgerReconciliation (0.31s)\n    ledger_test.go:204: balance mismatch for account 9917: expected 0, got -0.01\nFAIL",
        "previous_attempts": ["changed rounding mode: same failure", "edited the test fixture: same failure", "reverted fixture: same failure"],
    },
    "questions": [{"id": q["into"], "ask": q["ask"], "kind": q["kind"], "options": q.get("options"), "levels": q.get("levels")}
                  for q in profile["config"]["questions"]],
}
req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
if os.environ.get("ADAPTER_TOKEN"):
    req.add_header("Authorization", "Bearer " + os.environ["ADAPTER_TOKEN"])
resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
min_conf = profile["config"]["minConfidence"]
for a in resp["answers"]:
    flag = "" if a["confidence"] >= min_conf else f"   <- below minConfidence {min_conf}: cloud_stack would refuse this answer"
    print(f"{a['id']:<17} {str(a['answer']):<16} confidence={a['confidence']:.2f}{flag}")
