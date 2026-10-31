#!/usr/bin/env bash
# OpenJev toolkit installer: Apple silicon (MLX) or Linux + NVIDIA (vLLM).
#
#   curl -fsSL https://raw.githubusercontent.com/geekychris/open_jev_toolkit/main/scripts/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --profile flash-4bit
#
# Run from outside a checkout, it clones the repo into $OPENJEV_HOME (default ~/open_jev_toolkit)
# and re-runs itself from there. Inside a checkout it:
#   1. installs uv if missing and builds runtime/venv (Python 3.12; pinned MLX or vLLM stack)
#   2. downloads the chosen OpenJev build and the upstream helper (shim.py), verifying the helper hash
#   3. records the profile in runtime/active.env and runs a self-check
#
# Options:
#   --profile P        Apple silicon: 27b-8bit | 27b-4bit | flash-8bit | flash-4bit
#                      NVIDIA:        27b-fp8 | flash-fp8          (default: by memory)
#   --vllm-image IMG   NVIDIA: run vLLM from this Docker image instead of installing it in the venv
#                      (e.g. vllm/vllm-openai:v0.29.0, or an NGC vLLM image on DGX Spark)
#   --port N           port for the API (saved in runtime/active.env; default 3000)
#   --listen ADDR      address to bind (default 127.0.0.1). Anything else, e.g. 0.0.0.0, also
#                      generates a bearer token (SHIM_TOKEN in runtime/active.env) unless one is saved
#   --no-download      set up the venv only
#   --no-check         skip the self-check
set -euo pipefail

REPO_URL="${OPENJEV_REPO_URL:-https://github.com/geekychris/open_jev_toolkit.git}"
OPENJEV_HOME="${OPENJEV_HOME:-$HOME/open_jev_toolkit}"

# Piped from curl, or run from a copy outside a checkout: clone and hand over.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -z "$SELF_DIR" ] || [ ! -f "$SELF_DIR/lib.sh" ]; then
  command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
  if [ -d "$OPENJEV_HOME/.git" ]; then
    echo "==> updating $OPENJEV_HOME"; git -C "$OPENJEV_HOME" pull --ff-only
  else
    echo "==> cloning $REPO_URL into $OPENJEV_HOME"; git clone "$REPO_URL" "$OPENJEV_HOME"
  fi
  exec bash "$OPENJEV_HOME/scripts/install.sh" "$@"
fi

. "$SELF_DIR/lib.sh"

PROFILE=""; DOWNLOAD=1; CHECK=1; IMAGE="${VLLM_IMAGE:-}"; SET_PORT=""; SET_HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --vllm-image) IMAGE="$2"; shift 2 ;;
    --port) SET_PORT="$2"; shift 2 ;;
    --listen) SET_HOST="$2"; shift 2 ;;
    --no-download) DOWNLOAD=0; shift ;;
    --no-check) CHECK=0; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
done

case "$PLATFORM" in
  mlx|cuda) ;;
  *) die "no supported accelerator: this installer needs Apple silicon (MLX) or Linux with an NVIDIA GPU and driver (nvidia-smi)" ;;
esac

PROFILE="${PROFILE:-$(default_profile)}"
profile "$PROFILE" || exit 1
MEM=$(model_mem_gb)
if [ "$PLATFORM" = cuda ]; then
  GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
  say "profile $PROFILE: $MODEL_REPO (~${SIZE_GB} GB download, FP8 on load); GPU: ${GPU:-unknown}, ${MEM} GB for the model"
  [ -n "$(gpu_mem_gb)" ] || say "unified memory (e.g. DGX Spark): vLLM's share is sized at each start from the memory available then"
else
  say "profile $PROFILE: $MODEL_REPO (~${SIZE_GB} GB), this Mac has ${MEM} GB"
fi
[ "$MEM" -ge "$MIN_RAM_GB" ] || warn "$PROFILE wants ${MIN_RAM_GB} GB or more; consider a flash profile"

# 1. Python runtime
if ! command -v uv >/dev/null; then
  say "installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
mkdir -p "$RUNTIME" "$MODELS_DIR" "$HELPERS_DIR"
[ -x "$PY" ] || { say "creating $VENV"; uv venv "$VENV" --python 3.12 -q; }
if [ "$PLATFORM" = mlx ]; then
  say "installing pinned MLX stack + toolkit client"
  uv pip install -q --python "$PY" -r "$TOOLKIT_HOME/requirements.txt"
elif [ -n "$IMAGE" ]; then
  command -v docker >/dev/null || die "--vllm-image needs docker"
  docker info 2>/dev/null | grep -qi nvidia || warn "docker shows no NVIDIA runtime; install the NVIDIA Container Toolkit"
  say "installing the helper's packages; vLLM runs from $IMAGE"
  uv pip install -q --python "$PY" -r "$TOOLKIT_HOME/requirements-helper.txt"
  say "pulling $IMAGE"; docker pull -q "$IMAGE" >/dev/null
else
  say "installing pinned vLLM stack (torch matched to the driver; this takes a while)"
  uv pip install -q --python "$PY" --torch-backend=auto -r "$TOOLKIT_HOME/requirements-cuda.txt" \
    || die "vLLM install failed. On machines without prebuilt wheels (some aarch64 systems), use a container: --vllm-image vllm/vllm-openai:v0.29.0"
fi
uv pip install -q --python "$PY" -e "$TOOLKIT_HOME"

# 2. Model + helper
if [ "$DOWNLOAD" = 1 ]; then
  FREE_GB=$(free_gb "$RUNTIME")
  [ "$FREE_GB" -gt "$SIZE_GB" ] || die "need ~${SIZE_GB} GB free in $RUNTIME, have ${FREE_GB} GB"
  say "downloading $MODEL_REPO@${MODEL_REV:0:8} -> $MODEL_DIR (resumable)"
  if [ "$PLATFORM" = cuda ]; then
    # The full repo is the model and carries helper/; skip the model card images.
    "$VENV/bin/hf" download "$MODEL_REPO" --revision "$MODEL_REV" --local-dir "$MODEL_DIR" --exclude "assets/*" --quiet >/dev/null
  else
    "$VENV/bin/hf" download "$MODEL_REPO" --revision "$MODEL_REV" --local-dir "$MODEL_DIR" --quiet >/dev/null
    say "downloading helper from $HELPER_REPO@${HELPER_REV:0:8}"
    "$VENV/bin/hf" download "$HELPER_REPO" helper/shim.py helper/shim_mlx.py --revision "$HELPER_REV" --local-dir "$HELPER_DIR" --quiet >/dev/null
  fi
  SHA=$(sha256 "$HELPER_DIR/helper/shim.py" | cut -c1-8)
  [ "$SHA" = "$HELPER_SHA_PREFIX" ] || die "helper/shim.py hash $SHA is not the frozen $HELPER_SHA_PREFIX"
fi

active_set OPENJEV_PROFILE "$PROFILE"
active_set OPENJEV_VLLM_IMAGE "$IMAGE"
if [ -n "$SET_PORT" ]; then active_set OPENJEV_PORT "$SET_PORT"; export OPENJEV_PORT="$SET_PORT"; fi
if [ -n "$SET_HOST" ]; then active_set OPENJEV_HOST "$SET_HOST"; export OPENJEV_HOST="$SET_HOST"; fi
case "$(active_get OPENJEV_HOST)" in
  ''|127.0.0.1|localhost) ;;
  *) if [ -z "$(active_get SHIM_TOKEN)" ]; then
       active_set SHIM_TOKEN "$("$PY" -c 'import secrets; print(secrets.token_hex(24))')"
       say "generated a bearer token (SHIM_TOKEN in $ACTIVE_ENV)"
     fi
     export SHIM_TOKEN; SHIM_TOKEN=$(active_get SHIM_TOKEN) ;;
esac
say "active profile: $PROFILE; settings in $ACTIVE_ENV"

# 3. Self-check: loads the model and scores one fixed question (MLX: in-process; NVIDIA: through
#    a temporary vLLM + helper, which takes a few minutes the first time)
if [ "$DOWNLOAD" = 1 ] && [ "$CHECK" = 1 ]; then
  say "self-check (loads the model once)"
  "$TOOLKIT_HOME/scripts/serve.sh" selfcheck
fi

cat <<EOF

Done. Next:
  $TOOLKIT_HOME/scripts/serve.sh start        # API on http://127.0.0.1:3000/v1/systemone
  $TOOLKIT_HOME/examples/01_curl.sh
  $PY $TOOLKIT_HOME/examples/02_basic.py
EOF
