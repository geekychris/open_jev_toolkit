#!/usr/bin/env bash
# The raw API: one state, three typed questions, one request.
set -euo pipefail
URL="${OPENJEV_URL:-http://127.0.0.1:3000}"
curl -s "$URL/v1/systemone" -H 'Content-Type: application/json' -d '{
  "model": "openjev",
  "state": "Customer message: I was charged twice for my order last week and nobody has replied.",
  "questions": {
    "route":   {"type": "choice", "instructions": "Which team should handle this?",
                "criteria": {"billing": null, "shipping": null, "technical": null}},
    "angry":   {"type": "noul",   "instructions": "Is the customer angry?"},
    "urgency": {"type": "score",  "instructions": "How urgent is this?",
                "criteria": ["can wait", "this week", "today", "right now"]}
  }
}' | python3 -m json.tool
