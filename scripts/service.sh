#!/usr/bin/env bash
# Run the OpenJev API as a systemd user service (Linux): starts at boot, restarts on failure.
#
#   scripts/service.sh install     write ~/.config/systemd/user/openjev.service, enable it, start it,
#                                  and wait until the API answers
#   scripts/service.sh uninstall | start | stop | restart | status | logs
#
#   scripts/service.sh playground install [--debug] [--port N]
#                                  the playground as a second service, on the network (0.0.0.0:3020).
#                                  With a token (PLAYGROUND_TOKEN, generated and saved) unless --debug
#   scripts/service.sh playground uninstall | start | stop | restart | status | logs
#
# The service runs `scripts/serve.sh fg` with the settings in runtime/active.env (profile, port,
# listen address, token). Starting at boot without a login needs lingering for your user:
# `sudo loginctl enable-linger $USER` (install tries without sudo first).
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

UNIT=openjev.service
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
command -v systemctl >/dev/null || die "systemd not found; use scripts/serve.sh start instead"
sc() { systemctl --user "$@"; }

restarts() { sc show -p NRestarts --value "$UNIT" 2>/dev/null || echo 0; }

wait_healthy() {
  local t=0 limit=$(( ${VLLM_TIMEOUT:-1200} + 300 )) r0; r0=$(restarts)
  say "waiting for the API (first start loads the model; can take several minutes)"
  until "$TOOLKIT_HOME/scripts/serve.sh" health; do
    # A crash shows up as an automatic restart (Restart=on-failure), not as a failed unit.
    if sc is-failed -q "$UNIT" || [ "$(restarts)" != "$r0" ]; then
      tail -30 "$VLLM_LOG_FILE" >&2 2>/dev/null || true
      journalctl --user -u "$UNIT" -n 30 --no-pager >&2 || true
      sc stop "$UNIT" || true
      die "service failed and was stopped; fix the cause, then: scripts/service.sh start"
    fi
    [ "$t" -ge "$limit" ] && die "not ready after ${limit}s; see: scripts/service.sh logs"
    sleep 10; t=$((t + 10))
    [ $((t % 60)) -eq 0 ] && say "still loading (${t}s): $(tail -1 "$VLLM_LOG_FILE" 2>/dev/null | cut -c1-120)"
  done
  say "API ready after ${t}s"
}

# The playground service: scripts/playground.sh with the PLAYGROUND_* settings in runtime/active.env.
playground() {
  local unit=openjev-playground.service port
  pg_up() {   # 200 (debug) or 401 (token) from loopback means it is serving
    local code; code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$(active_get PLAYGROUND_PORT)/api/status" || true)
    [ "$code" = 200 ] || [ "$code" = 401 ]
  }
  pg_wait() {
    for _ in $(seq 1 30); do pg_up && return 0; sleep 1; done
    journalctl --user -u "$unit" -n 20 --no-pager >&2 || true; die "playground did not start"
  }
  case "${1:-status}" in
    install)
      shift
      local debug=0; port=$(active_get PLAYGROUND_PORT); port=${port:-3020}
      while [ $# -gt 0 ]; do
        case "$1" in --debug) debug=1; shift ;; --port) port="$2"; shift 2 ;; *) die "unknown option $1" ;; esac
      done
      [ -x "$PY" ] || die "no runtime; run scripts/install.sh first"
      active_set PLAYGROUND_PORT "$port"
      [ -n "$(active_get PLAYGROUND_HOST)" ] || active_set PLAYGROUND_HOST 0.0.0.0
      if [ "$debug" = 1 ]; then active_set PLAYGROUND_DEBUG 1
      else
        active_set PLAYGROUND_DEBUG ""
        [ -n "$(active_get PLAYGROUND_TOKEN)" ] || active_set PLAYGROUND_TOKEN "$("$PY" -c 'import secrets; print(secrets.token_urlsafe(24))')"
      fi
      # Samples saved through a server's playground live under runtime/ (not in the git checkout,
      # so deploys can update it); the first install copies the seed set.
      if [ -z "$(active_get OPENJEV_SAMPLES)" ]; then
        active_set OPENJEV_SAMPLES "$RUNTIME/samples"
        mkdir -p "$RUNTIME/samples"; cp -n "$TOOLKIT_HOME"/samples/*.json "$RUNTIME/samples/" 2>/dev/null || true
      fi
      mkdir -p "$UNIT_DIR"
      cat > "$UNIT_DIR/$unit" <<EOF
[Unit]
Description=OpenJev playground (open_jev_toolkit)
After=network-online.target openjev.service

[Service]
Type=simple
WorkingDirectory=$TOOLKIT_HOME
ExecStart=$TOOLKIT_HOME/scripts/playground.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
      sc daemon-reload; sc enable -q "$unit"; sc restart "$unit"; pg_wait
      if [ "$debug" = 1 ]; then say "playground up on port $port, DEBUG MODE: no token"
      else say "playground up on port $port; token: PLAYGROUND_TOKEN in $ACTIVE_ENV"; fi ;;
    uninstall) sc disable --now -q "$unit" 2>/dev/null || true; rm -f "$UNIT_DIR/$unit"; sc daemon-reload; say "removed $unit" ;;
    start|restart) sc "$1" "$unit"; pg_wait; say "playground up" ;;
    stop) sc stop "$unit"; say "stopped" ;;
    status) sc status "$unit" --no-pager -n 3 || true ;;
    logs) journalctl --user -u "$unit" -f ;;
    *) die "usage: service.sh playground install [--debug] [--port N] | uninstall | start | stop | restart | status | logs" ;;
  esac
}

case "${1:-status}" in
  playground) shift; playground "$@" ;;
  install)
    [ -x "$PY" ] || die "no runtime; run scripts/install.sh first"
    if running_pid=$(cat "$PID_FILE" 2>/dev/null) && kill -0 "$running_pid" 2>/dev/null && ! sc is-active -q "$UNIT"; then
      say "stopping the server started by serve.sh"; "$TOOLKIT_HOME/scripts/serve.sh" stop
    fi
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$UNIT" <<EOF
[Unit]
Description=OpenJev decision API (open_jev_toolkit)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$TOOLKIT_HOME
ExecStart=$TOOLKIT_HOME/scripts/serve.sh fg
ExecStopPost=$TOOLKIT_HOME/scripts/serve.sh stop
Restart=on-failure
RestartSec=30
TimeoutStartSec=infinity
TimeoutStopSec=60
KillMode=control-group

[Install]
WantedBy=default.target
EOF
    sc daemon-reload
    sc enable -q "$UNIT"
    sc restart "$UNIT"
    say "installed $UNIT_DIR/$UNIT"
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
      if loginctl enable-linger "$USER" 2>/dev/null; then say "enabled lingering: the service starts at boot"
      else warn "the service stops when you log out and won't start at boot until you run: sudo loginctl enable-linger $USER"; fi
    fi
    wait_healthy ;;
  uninstall)
    sc disable --now -q "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT"; sc daemon-reload; say "removed $UNIT" ;;
  start|restart) sc "$1" "$UNIT"; wait_healthy ;;
  stop) sc stop "$UNIT"; say "stopped" ;;
  status) sc status "$UNIT" --no-pager -n 0 || true; "$TOOLKIT_HOME/scripts/serve.sh" status ;;
  logs) say "vLLM log: $VLLM_LOG_FILE"; journalctl --user -u "$UNIT" -f ;;
  *) die "usage: service.sh install|uninstall|start|stop|restart|status|logs, or service.sh playground ..." ;;
esac
