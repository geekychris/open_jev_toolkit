#!/usr/bin/env bash
# Run the OpenJev decision API locally (upstream helper over MLX).
#
#   scripts/serve.sh start [--profile P] [--port N]   start in the background, wait until ready
#   scripts/serve.sh stop | restart | status | logs
#   scripts/serve.sh fg [--profile P] [--port N]      run in the foreground
#   scripts/serve.sh selfcheck [--profile P]          score one fixed question, no server
#
# Env: OPENJEV_PORT (3000), OPENJEV_HOST (127.0.0.1), SHIM_TOKEN (require a bearer token).
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

CMD="${1:-status}"; [ $# -gt 0 ] && shift
PROFILE=""; PORT="${OPENJEV_PORT:-3000}"; HOST="${OPENJEV_HOST:-127.0.0.1}"
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    *) die "unknown option $1" ;;
  esac
done
PROFILE="${PROFILE:-$(active_profile)}"
profile "$PROFILE" || exit 1

running() { [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

helper_cmd() {
  [ -x "$PY" ] || die "no runtime; run scripts/install.sh first"
  [ -f "$MODEL_DIR/config.json" ] || die "$MODEL_REPO not downloaded; run scripts/install.sh --profile $PROFILE"
  [ -f "$HELPER_DIR/helper/shim_mlx.py" ] || die "helper missing; run scripts/install.sh --profile $PROFILE"
  # Calibration constants from the model card for this build; do not change them.
  export TOKENIZER="$MODEL_DIR" SHIM_MODEL="$MODEL_DIR" \
         READOUT_T="$READOUT_T" READOUT_NOUL_T="$READOUT_NOUL_T" READOUT_NOUL_BIAS=0 \
         READOUT_TARGETED=1 READOUT_INSTR_STYLE=pyrepr SHIM_STAGGER=1
  HELPER_ARGS=("$HELPER_DIR/helper/shim_mlx.py" --helper "$HELPER_DIR/helper/shim.py" --model "$MODEL_DIR")
}

wait_ready() {
  for _ in $(seq 1 180); do
    curl -fs "http://$HOST:$PORT/v1/version" >/dev/null 2>&1 && return 0
    running || { tail -20 "$LOG_FILE" >&2; die "server exited during startup"; }
    sleep 1
  done
  die "server not ready after 180s; see $LOG_FILE"
}

case "$CMD" in
  start)
    running && { say "already running (pid $(cat "$PID_FILE")) on :$PORT"; exit 0; }
    helper_cmd
    say "starting $PROFILE on http://$HOST:$PORT (log: $LOG_FILE)"
    nohup "$PY" "${HELPER_ARGS[@]}" --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    wait_ready
    say "ready: $(curl -fs "http://$HOST:$PORT/v1/version")" ;;
  fg)
    helper_cmd; exec "$PY" "${HELPER_ARGS[@]}" --host "$HOST" --port "$PORT" ;;
  selfcheck)
    helper_cmd; "$PY" "${HELPER_ARGS[@]}" --selfcheck ;;
  stop)
    if running; then kill "$(cat "$PID_FILE")"; rm -f "$PID_FILE"; say "stopped"; else say "not running"; fi ;;
  restart)
    "$0" stop; "$0" start --profile "$PROFILE" --port "$PORT" ;;
  status)
    if running; then say "running (pid $(cat "$PID_FILE")), profile $PROFILE"; curl -fs "http://$HOST:$PORT/v1/version" && echo
    else say "not running (profile $PROFILE)"; fi ;;
  logs)
    tail -f "$LOG_FILE" ;;
  *) die "usage: serve.sh start|stop|restart|status|logs|fg|selfcheck" ;;
esac
