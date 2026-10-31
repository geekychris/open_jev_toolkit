#!/usr/bin/env bash
# Run the OpenJev API as a systemd user service (Linux): starts at boot, restarts on failure.
#
#   scripts/service.sh install     write ~/.config/systemd/user/openjev.service, enable it, start it,
#                                  and wait until the API answers
#   scripts/service.sh uninstall | start | stop | restart | status | logs
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

wait_healthy() {
  local t=0 limit=$(( ${VLLM_TIMEOUT:-1200} + 300 ))
  say "waiting for the API (first start loads the model; can take several minutes)"
  until "$TOOLKIT_HOME/scripts/serve.sh" health; do
    if sc is-failed -q "$UNIT"; then
      journalctl --user -u "$UNIT" -n 30 --no-pager >&2 || true
      die "service failed; see: scripts/service.sh logs"
    fi
    [ "$t" -ge "$limit" ] && die "not ready after ${limit}s; see: scripts/service.sh logs"
    sleep 10; t=$((t + 10))
    [ $((t % 60)) -eq 0 ] && say "still loading (${t}s): $(tail -1 "$VLLM_LOG_FILE" 2>/dev/null | cut -c1-120)"
  done
  say "API ready after ${t}s"
}

case "${1:-status}" in
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
  *) die "usage: service.sh install|uninstall|start|stop|restart|status|logs" ;;
esac
