#!/usr/bin/env bash
# Run the OpenJev decision API locally: the upstream helper over MLX (Apple silicon), or over a
# vLLM server (Linux + NVIDIA). Both serve the same API on :3000.
#
#   scripts/serve.sh start [--profile P] [--port N]   start in the background, wait until ready
#   scripts/serve.sh stop | restart | status | logs
#   scripts/serve.sh fg [--profile P] [--port N]      run in the foreground (NVIDIA: vLLM still in the background)
#   scripts/serve.sh selfcheck [--profile P]          score one fixed question
#   scripts/serve.sh cmd [--profile P]                print the commands start would run
#   scripts/serve.sh health                           exit 0 if the API answers
#
# Env: OPENJEV_PORT (3000), OPENJEV_HOST (127.0.0.1), SHIM_TOKEN (require a bearer token).
# Any of these can be saved in runtime/active.env (install.sh --port/--listen does that); the
# environment wins over the file.
# NVIDIA only:
#   OPENJEV_VLLM_PORT (8000)  vLLM's port; always bound to 127.0.0.1
#   VLLM_GPU_MEM              --gpu-memory-utilization (0.90; on unified-memory GPUs such as DGX Spark,
#                             sized at start from the memory actually available)
#   VLLM_IMAGE                run vLLM from this Docker image (default: the one given to install.sh, if any)
#   VLLM_EXTRA_ARGS           extra vllm serve flags (changes what was measured; for troubleshooting)
#   VLLM_TIMEOUT              seconds to wait for vLLM to load (1200)
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

VLLM_PORT="${OPENJEV_VLLM_PORT:-8000}"
IMAGE="${VLLM_IMAGE:-$(active_get OPENJEV_VLLM_IMAGE)}"
UNIFIED=0; if [ "$BACKEND" = cuda ] && [ -z "$(gpu_mem_gb)" ]; then UNIFIED=1; fi
VLLM_GPU_MEM="${VLLM_GPU_MEM:-}"

# Where to probe the helper: the bind address, or loopback when it binds every interface.
PROBE="$HOST"; [ "$HOST" = 0.0.0.0 ] && PROBE=127.0.0.1

# GET the helper's /v1/version (with the token when one is set).
version() { curl -fs ${SHIM_TOKEN:+-H "Authorization: Bearer $SHIM_TOKEN"} "http://$PROBE:$PORT/v1/version"; }

running() { [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; }

vllm_running() {
  if [ -n "$IMAGE" ]; then [ "$(docker inspect -f '{{.State.Running}}' "$VLLM_CONTAINER" 2>/dev/null)" = true ]
  else [ -f "$VLLM_PID_FILE" ] && kill -0 "$(cat "$VLLM_PID_FILE")" 2>/dev/null; fi
}

helper_cmd() {
  [ -x "$PY" ] || die "no runtime; run scripts/install.sh first"
  [ -f "$MODEL_DIR/config.json" ] || die "$MODEL_REPO not downloaded; run scripts/install.sh --profile $PROFILE"
  # Calibration constants from the model card for this build; do not change them.
  export TOKENIZER="$MODEL_DIR" SHIM_MODEL="$MODEL_DIR" \
         READOUT_T="$READOUT_T" READOUT_NOUL_T="$READOUT_NOUL_T" READOUT_NOUL_BIAS=0 \
         READOUT_TARGETED=1 READOUT_INSTR_STYLE=pyrepr SHIM_STAGGER=1
  if [ "$BACKEND" = cuda ]; then
    [ -f "$HELPER_DIR/helper/shim.py" ] || die "helper missing; run scripts/install.sh --profile $PROFILE"
    export VLLM="http://127.0.0.1:$VLLM_PORT/v1"
    HELPER_ARGS=("$HELPER_DIR/helper/shim.py")
  else
    [ -f "$HELPER_DIR/helper/shim_mlx.py" ] || die "helper missing; run scripts/install.sh --profile $PROFILE"
    HELPER_ARGS=("$HELPER_DIR/helper/shim_mlx.py" --helper "$HELPER_DIR/helper/shim.py" --model "$MODEL_DIR")
  fi
}

# The measured vLLM flags from upstream serve/SERVE.md. Only the memory share is adjustable: it sizes
# the KV cache and does not change answers. --served-model-name qwen and --max-logprobs 64 are what
# the helper relies on.
vllm_args() {
  local model="$1"
  VLLM_ARGS=(serve "$model" --host 127.0.0.1 --port "$VLLM_PORT" --served-model-name qwen
    --enable-prefix-caching --max-model-len 16384 --gpu-memory-utilization "$VLLM_GPU_MEM"
    --limit-mm-per-prompt '{"image":1}' --trust-remote-code --max-num-seqs "$VLLM_MAX_SEQS"
    --max-logprobs 64 --gdn-prefill-backend triton --quantization fp8)
  # shellcheck disable=SC2206
  [ -n "${VLLM_EXTRA_ARGS:-}" ] && VLLM_ARGS+=($VLLM_EXTRA_ARGS)
  return 0
}

docker_cmd() {
  DOCKER_ARGS=(run -d --name "$VLLM_CONTAINER" --gpus all --ipc=host --network host
    -v "$MODEL_DIR:/model:ro" --entrypoint vllm "$IMAGE")
}

meminfo_gb() { awk -v k="$1:" '$1==k{print int($2/1048576)}' /proc/meminfo; }

# The share of memory vLLM may take. On a discrete GPU, upstream's 0.90. On unified memory (DGX Spark)
# the GPU shares RAM with everything else, so: what is available now minus headroom, at most 0.60,
# and at least what the profile needs.
size_vllm_memory() {
  [ -n "$VLLM_GPU_MEM" ] && return 0
  if [ "$UNIFIED" = 0 ]; then VLLM_GPU_MEM=0.90; return 0; fi
  local total avail budget headroom="${VLLM_HEADROOM_GB:-12}"
  total=$(meminfo_gb MemTotal); avail=$(meminfo_gb MemAvailable)
  budget=$(( avail - headroom ))
  if [ "$budget" -gt $(( total * 60 / 100 )) ]; then budget=$(( total * 60 / 100 )); fi
  [ "$budget" -ge "$VLLM_NEED_GB" ] || die "$PROFILE needs ~${VLLM_NEED_GB} GB for vLLM; ${avail} GB of ${total} GB is available (keeping ${headroom} GB headroom). Free memory, or use --profile flash-fp8."
  VLLM_GPU_MEM=$(awk -v b="$budget" -v t="$total" 'BEGIN{printf "%.2f", b/t}')
  say "unified memory: ${avail} GB available of ${total} GB; vLLM gets ${budget} GB (--gpu-memory-utilization $VLLM_GPU_MEM)"
}

# On unified memory CUDA counts only free pages, not reclaimable file cache, and vLLM refuses to
# start when its share exceeds CUDA's free figure. Holding memory briefly makes the kernel drop clean
# cache pages to make room, and that memory stays free once released (the effect of drop_caches,
# without root). The amount is fixed up front (the shortfall, never more than available minus 8 GB),
# so other processes are not pushed into swap.
reclaim_cache() {
  [ "$UNIFIED" = 1 ] || return 0
  local want free grab limit
  want=$(awk -v g="$VLLM_GPU_MEM" -v t="$(meminfo_gb MemTotal)" 'BEGIN{printf "%d", g*t + 3}')
  free=$(meminfo_gb MemFree)
  [ "$free" -ge "$want" ] && return 0
  grab=$(( want - free + 2 )); limit=$(( $(meminfo_gb MemAvailable) - 8 ))
  if [ "$grab" -gt "$limit" ]; then grab=$limit; fi
  [ "$grab" -gt 0 ] || return 0
  say "freeing page cache: ${free} GB free, vLLM needs ${want} GB; holding ${grab} GB briefly"
  "$PY" - "$grab" <<'EOF'
import sys
def available_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1048576
hold = []
for _ in range(int(sys.argv[1])):
    if available_gb() < 6:   # safety stop if something else grew meanwhile
        break
    hold.append(bytearray(1 << 30))
del hold
EOF
  free=$(meminfo_gb MemFree)
  if [ "$free" -ge "$want" ]; then say "now ${free} GB free"
  else warn "only ${free} GB free after freeing cache (vLLM wants ${want} GB); it may refuse to start"; fi
}

start_vllm() {
  vllm_running && { say "vLLM already running on 127.0.0.1:$VLLM_PORT"; return 0; }
  size_vllm_memory
  reclaim_cache
  if [ -n "$IMAGE" ]; then
    vllm_args /model; docker_cmd
    docker rm -f "$VLLM_CONTAINER" >/dev/null 2>&1 || true
    say "starting vLLM ($IMAGE) for $PROFILE on 127.0.0.1:$VLLM_PORT (log: docker logs $VLLM_CONTAINER)"
    docker "${DOCKER_ARGS[@]}" "${VLLM_ARGS[@]}" >/dev/null
  else
    [ -x "$VENV/bin/vllm" ] || die "vLLM is not installed in $VENV; run scripts/install.sh (or set VLLM_IMAGE)"
    vllm_args "$MODEL_DIR"
    say "starting vLLM for $PROFILE on 127.0.0.1:$VLLM_PORT (log: $VLLM_LOG_FILE)"
    # setsid: vLLM spawns engine processes; its own process group lets stop take them all down.
    setsid nohup "$VENV/bin/vllm" "${VLLM_ARGS[@]}" >"$VLLM_LOG_FILE" 2>&1 < /dev/null &
    echo $! > "$VLLM_PID_FILE"
  fi
  say "waiting for vLLM to load and quantise the model (first start: several minutes)"
  local t=0 limit="${VLLM_TIMEOUT:-1200}"
  until curl -fs "http://127.0.0.1:$VLLM_PORT/v1/models" >/dev/null 2>&1; do
    vllm_running || { vllm_logs | tail -30 >&2; die "vLLM exited during startup"; }
    [ "$t" -ge "$limit" ] && die "vLLM not ready after ${limit}s; see $(vllm_log_hint)"
    sleep 5; t=$((t + 5))
  done
  say "vLLM ready after ${t}s"
}

stop_vllm() {
  if [ -n "$IMAGE" ]; then
    docker rm -f "$VLLM_CONTAINER" >/dev/null 2>&1 && say "vLLM container removed" || true
    return 0
  fi
  vllm_running || { rm -f "$VLLM_PID_FILE"; return 0; }
  local pid; pid=$(cat "$VLLM_PID_FILE")
  kill -- "-$pid" 2>/dev/null || kill "$pid"
  for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  kill -9 -- "-$pid" 2>/dev/null || true
  rm -f "$VLLM_PID_FILE"; say "vLLM stopped"
}

vllm_logs() { if [ -n "$IMAGE" ]; then docker logs "$VLLM_CONTAINER" 2>&1; else cat "$VLLM_LOG_FILE" 2>/dev/null; fi; }
vllm_log_hint() { if [ -n "$IMAGE" ]; then echo "docker logs $VLLM_CONTAINER"; else echo "$VLLM_LOG_FILE"; fi; }

vllm_note() { [ "$BACKEND" = cuda ] && echo " (vLLM stays loaded; scripts/serve.sh stop stops it)" || true; }

wait_ready() {
  for _ in $(seq 1 180); do
    version >/dev/null 2>&1 && return 0
    running || { tail -20 "$LOG_FILE" >&2; die "server exited during startup$(vllm_note)"; }
    sleep 1
  done
  die "server not ready after 180s; see $LOG_FILE$(vllm_note)"
}

start_helper() {
  running && { say "already running (pid $(cat "$PID_FILE")) on :$PORT"; return 0; }
  helper_cmd
  [ "$BACKEND" = cuda ] && start_vllm
  say "starting $PROFILE on http://$HOST:$PORT (log: $LOG_FILE)"
  nohup "$PY" "${HELPER_ARGS[@]}" --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  wait_ready
  say "ready: $(version)"
}

stop_all() {
  if running; then kill "$(cat "$PID_FILE")"; rm -f "$PID_FILE"; say "stopped"; else say "not running"; fi
  [ "$BACKEND" = cuda ] && stop_vllm
  return 0
}

case "$CMD" in
  start) start_helper ;;
  fg)
    helper_cmd; [ "$BACKEND" = cuda ] && start_vllm
    echo $$ > "$PID_FILE"   # exec keeps this pid, so status and stop see the foreground helper
    say "helper on http://$HOST:$PORT${SHIM_TOKEN:+ (bearer token required)}"
    exec "$PY" "${HELPER_ARGS[@]}" --host "$HOST" --port "$PORT" ;;
  selfcheck)
    if [ "$BACKEND" = mlx ]; then helper_cmd; exec "$PY" "${HELPER_ARGS[@]}" --selfcheck; fi
    # NVIDIA: the helper has no in-process check, so ask the live API one fixed question.
    was_running=0; running && was_running=1
    start_helper
    OPENJEV_URL="http://$PROBE:$PORT" "$PY" - <<'EOF'
from openjev_client import Jev, choice, yesno
r = Jev().ask("I was charged twice for my order last week and nobody has replied.",
              route=choice("Which team should handle this?", ["billing", "shipping", "technical"]),
              angry=yesno("Is the customer angry or frustrated?"))
print(f"route={r['route'].choice} ({r['route'].confidence:.2f})  angry p={r['angry'].p:.2f}  {r.elapsed_ms:.0f} ms")
assert r["route"].choice == "billing", "self-check expected route=billing"
EOF
    [ "$was_running" = 1 ] || stop_all ;;
  cmd)
    helper_cmd
    if [ "$BACKEND" = cuda ]; then
      if [ "$UNIFIED" = 1 ] && [ -r /proc/meminfo ]; then size_vllm_memory >&2; fi
      VLLM_GPU_MEM="${VLLM_GPU_MEM:-0.90}"
      if [ -n "$IMAGE" ]; then vllm_args /model; docker_cmd; printf '%q ' docker "${DOCKER_ARGS[@]}" "${VLLM_ARGS[@]}"
      else vllm_args "$MODEL_DIR"; printf '%q ' "$VENV/bin/vllm" "${VLLM_ARGS[@]}"; fi
      echo; echo
      printf 'VLLM=%q ' "$VLLM"
    fi
    printf '%s ' TOKENIZER="$TOKENIZER" SHIM_MODEL="$SHIM_MODEL" READOUT_T="$READOUT_T" READOUT_NOUL_T="$READOUT_NOUL_T" \
      READOUT_NOUL_BIAS=0 READOUT_TARGETED=1 READOUT_INSTR_STYLE=pyrepr SHIM_STAGGER=1
    printf '%q ' "$PY" "${HELPER_ARGS[@]}" --host "$HOST" --port "$PORT"; echo ;;
  health) version >/dev/null 2>&1 ;;
  stop) stop_all ;;
  restart)
    "$0" stop --profile "$PROFILE"; "$0" start --profile "$PROFILE" --port "$PORT" ;;
  status)
    if [ "$BACKEND" = cuda ]; then
      if vllm_running; then say "vLLM running on 127.0.0.1:$VLLM_PORT${IMAGE:+ (container $VLLM_CONTAINER)}"; else say "vLLM not running"; fi
    fi
    if running; then say "running (pid $(cat "$PID_FILE")), profile $PROFILE"; version && echo
    else say "not running (profile $PROFILE)"; fi ;;
  logs)
    if [ "$BACKEND" = cuda ]; then say "helper log: $LOG_FILE; vLLM log: $(vllm_log_hint)"; fi
    tail -f "$LOG_FILE" ;;
  *) die "usage: serve.sh start|stop|restart|status|logs|fg|selfcheck|cmd|health" ;;
esac
