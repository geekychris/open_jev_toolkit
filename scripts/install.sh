#!/usr/bin/env bash
# OpenJev toolkit installer (Apple silicon / MLX).
#
#   curl -fsSL https://raw.githubusercontent.com/geekychris/open_jev_toolkit/main/scripts/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --profile flash-4bit
#
# Run from outside a checkout, it clones the repo into $OPENJEV_HOME (default ~/open_jev_toolkit)
# and re-runs itself from there. Inside a checkout it:
#   1. installs uv if missing and builds runtime/venv (Python 3.12, pinned MLX stack)
#   2. downloads the chosen OpenJev build and the upstream helper (shim.py), verifying the helper hash
#   3. records the profile in runtime/active.env and runs a self-check
#
# Options:
#   --profile P     27b-8bit | 27b-4bit | flash-8bit | flash-4bit  (default: by RAM)
#   --no-download   set up the venv only
#   --no-check      skip the self-check
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

PROFILE=""; DOWNLOAD=1; CHECK=1
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --no-download) DOWNLOAD=0; shift ;;
    --no-check) CHECK=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown option $1" ;;
  esac
done

[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || die "this installer targets Apple silicon (MLX). For Linux + NVIDIA, follow serve/SERVE.md in openjev/openjev (vLLM)."

PROFILE="${PROFILE:-$(default_profile)}"
profile "$PROFILE" || exit 1
RAM=$(ram_gb)
say "profile $PROFILE: $MODEL_REPO (~${SIZE_GB} GB), this Mac has ${RAM} GB"
[ "$RAM" -ge "$MIN_RAM_GB" ] || warn "$PROFILE wants ${MIN_RAM_GB} GB RAM or more; consider --profile flash-4bit"

# 1. Python runtime
if ! command -v uv >/dev/null; then
  say "installing uv"; curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
mkdir -p "$RUNTIME" "$MODELS_DIR" "$HELPERS_DIR"
[ -x "$PY" ] || { say "creating $VENV"; uv venv "$VENV" --python 3.12 -q; }
say "installing pinned MLX stack + toolkit client"
uv pip install -q --python "$PY" -r "$TOOLKIT_HOME/requirements.txt"
uv pip install -q --python "$PY" -e "$TOOLKIT_HOME"

# 2. Model + helper
if [ "$DOWNLOAD" = 1 ]; then
  FREE_GB=$(df -g "$RUNTIME" | awk 'NR==2{print $4}')
  [ "$FREE_GB" -gt "$SIZE_GB" ] || die "need ~${SIZE_GB} GB free in $RUNTIME, have ${FREE_GB} GB"
  say "downloading $MODEL_REPO@${MODEL_REV:0:8} -> $MODEL_DIR (resumable)"
  "$VENV/bin/hf" download "$MODEL_REPO" --revision "$MODEL_REV" --local-dir "$MODEL_DIR" --quiet >/dev/null
  say "downloading helper from $HELPER_REPO@${HELPER_REV:0:8}"
  "$VENV/bin/hf" download "$HELPER_REPO" helper/shim.py helper/shim_mlx.py --revision "$HELPER_REV" --local-dir "$HELPER_DIR" --quiet >/dev/null
  SHA=$(shasum -a 256 "$HELPER_DIR/helper/shim.py" | cut -c1-8)
  [ "$SHA" = "$HELPER_SHA_PREFIX" ] || die "helper/shim.py hash $SHA is not the frozen $HELPER_SHA_PREFIX"
fi

echo "OPENJEV_PROFILE=$PROFILE" > "$ACTIVE_ENV"
say "active profile: $PROFILE (runtime/active.env)"

# 3. Self-check: loads the model and scores one fixed question without starting the server
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
