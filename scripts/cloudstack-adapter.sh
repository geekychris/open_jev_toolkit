#!/usr/bin/env bash
# The cloud_stack-compatible endpoint (openjev_client/cloudstack.py) in front of the OpenJev API.
#
#   scripts/cloudstack-adapter.sh start | stop | status | logs | fg
#
# Env: ADAPTER_HOST (127.0.0.1; 0.0.0.0 for containers), ADAPTER_PORT (3010),
#      ADAPTER_TOKEN (bearer token = cloud_stack's JEV_API_KEY), OPENJEV_URL (http://127.0.0.1:3000).
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

APID="$RUNTIME/adapter.pid"; ALOG="$RUNTIME/adapter.log"
HOST="${ADAPTER_HOST:-127.0.0.1}"; PORT="${ADAPTER_PORT:-3010}"
running() { [ -f "$APID" ] && kill -0 "$(cat "$APID")" 2>/dev/null; }
[ -x "$PY" ] || die "no runtime; run scripts/install.sh first"

case "${1:-status}" in
  start)
    running && { say "adapter already running (pid $(cat "$APID"))"; exit 0; }
    nohup "$PY" -m openjev_client.cloudstack --host "$HOST" --port "$PORT" >"$ALOG" 2>&1 &
    echo $! > "$APID"
    for _ in $(seq 1 20); do
      curl -fs "http://127.0.0.1:$PORT/health" ${ADAPTER_TOKEN:+-H "Authorization: Bearer $ADAPTER_TOKEN"} >/dev/null 2>&1 && break
      running || { cat "$ALOG" >&2; die "adapter exited"; }
      sleep 0.5
    done
    say "adapter on http://$HOST:$PORT/v1/decide (log: $ALOG)"
    curl -fs "http://127.0.0.1:$PORT/health" ${ADAPTER_TOKEN:+-H "Authorization: Bearer $ADAPTER_TOKEN"} >/dev/null \
      || warn "OpenJev API not answering yet; start it with scripts/serve.sh start" ;;
  stop) if running; then kill "$(cat "$APID")"; rm -f "$APID"; say "adapter stopped"; else say "adapter not running"; fi ;;
  status) if running; then say "adapter running (pid $(cat "$APID")) on :$PORT"; else say "adapter not running"; fi ;;
  logs) tail -f "$ALOG" ;;
  fg) exec "$PY" -m openjev_client.cloudstack --host "$HOST" --port "$PORT" ;;
  *) die "usage: cloudstack-adapter.sh start|stop|status|logs|fg" ;;
esac
