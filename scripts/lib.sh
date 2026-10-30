# Shared settings for the OpenJev toolkit scripts. Sourced, not executed.
#
# A "profile" is one OpenJev build plus the calibration constants its model card
# publishes. The READOUT_* values must match the build: they are what turns the
# letter scores into calibrated probabilities.
#
# Two backends serve the same API (POST /v1/systemone) through the same frozen helper (shim.py):
#   mlx   Apple silicon: shim_mlx.py runs the MLX build in-process.
#   cuda  Linux + NVIDIA: vLLM serves the 16-bit weights with online FP8 on 127.0.0.1:8000,
#         and shim.py runs in front of it (upstream serve/SERVE.md, the measured setup).

TOOLKIT_HOME="${TOOLKIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME="${OPENJEV_RUNTIME:-$TOOLKIT_HOME/runtime}"
VENV="$RUNTIME/venv"
PY="$VENV/bin/python"
MODELS_DIR="$RUNTIME/models"
HELPERS_DIR="$RUNTIME/helpers"
ACTIVE_ENV="$RUNTIME/active.env"
PID_FILE="$RUNTIME/server.pid"
LOG_FILE="$RUNTIME/server.log"
VLLM_PID_FILE="$RUNTIME/vllm.pid"
VLLM_LOG_FILE="$RUNTIME/vllm.log"
VLLM_CONTAINER=openjev-vllm

# The helper (shim.py) is frozen upstream; shim_mlx.py refuses any other version.
HELPER_SHA_PREFIX="81a22f1b"

PROFILES_MLX="27b-8bit 27b-4bit flash-8bit flash-4bit"
PROFILES_CUDA="27b-fp8 flash-fp8"
PROFILES="$PROFILES_MLX $PROFILES_CUDA"

profile() {
  BACKEND=mlx; VLLM_MAX_SEQS=""
  case "$1" in
    27b-8bit)
      MODEL_REPO=openjev/openjev-MLX;            MODEL_REV=a9dcc20aa827a6c7eae478f6ebb3b255bb135451
      HELPER_REPO=openjev/openjev;               HELPER_REV=1c341f65bfe5d50fdb935c71e9739c9e0938d6c4
      READOUT_T=0.85; READOUT_NOUL_T=1.829074;   SIZE_GB=27; MIN_RAM_GB=64 ;;
    27b-4bit)
      MODEL_REPO=openjev/openjev-MLX-4bit;       MODEL_REV=63aecab0abcba710f38270ddc1061fb2e3d8d143
      HELPER_REPO=openjev/openjev;               HELPER_REV=1c341f65bfe5d50fdb935c71e9739c9e0938d6c4
      READOUT_T=0.85; READOUT_NOUL_T=1.829074;   SIZE_GB=15; MIN_RAM_GB=32 ;;
    flash-8bit)
      MODEL_REPO=openjev/OpenJev-Flash-9B-MLX;   MODEL_REV=d4709a169392d36a3d6ad79592bfe7f4cadcad83
      HELPER_REPO=openjev/OpenJev-Flash-9B;      HELPER_REV=b77b0fff67c77a76c16d4da50a66d2fdbc124fae
      READOUT_T=1.07; READOUT_NOUL_T=1.074766;   SIZE_GB=10; MIN_RAM_GB=24 ;;
    flash-4bit)
      MODEL_REPO=openjev/OpenJev-Flash-9B-MLX-4bit; MODEL_REV=33cbf3727276b3fc99a08104c33b22bf1c5f05dd
      HELPER_REPO=openjev/OpenJev-Flash-9B;      HELPER_REV=b77b0fff67c77a76c16d4da50a66d2fdbc124fae
      READOUT_T=1.07; READOUT_NOUL_T=1.074766;   SIZE_GB=5;  MIN_RAM_GB=16 ;;
    # NVIDIA: the 16-bit repos (they include helper/), quantised to FP8 by vLLM at load.
    # MIN_RAM_GB is GPU memory here (system memory on unified-memory machines such as DGX Spark).
    27b-fp8)
      MODEL_REPO=openjev/openjev;                MODEL_REV=1c341f65bfe5d50fdb935c71e9739c9e0938d6c4
      HELPER_REPO=$MODEL_REPO;                   HELPER_REV=$MODEL_REV
      READOUT_T=0.85; READOUT_NOUL_T=1.829074;   SIZE_GB=55; MIN_RAM_GB=48
      BACKEND=cuda; VLLM_MAX_SEQS=256 ;;
    flash-fp8)
      MODEL_REPO=openjev/OpenJev-Flash-9B;       MODEL_REV=b77b0fff67c77a76c16d4da50a66d2fdbc124fae
      HELPER_REPO=$MODEL_REPO;                   HELPER_REV=$MODEL_REV
      READOUT_T=1.07; READOUT_NOUL_T=1.074766;   SIZE_GB=19; MIN_RAM_GB=24
      BACKEND=cuda; VLLM_MAX_SEQS=16 ;;
    *) echo "unknown profile '$1' (choose from: $(profiles_for "$PLATFORM"))" >&2; return 1 ;;
  esac
  MODEL_DIR="$MODELS_DIR/${MODEL_REPO#openjev/}"
  if [ "$BACKEND" = cuda ]; then HELPER_DIR="$MODEL_DIR"; else HELPER_DIR="$HELPERS_DIR/${HELPER_REPO#openjev/}"; fi
  [ "$BACKEND" = "$PLATFORM" ] || { echo "profile $1 needs the $BACKEND backend; this machine is $PLATFORM (profiles: $(profiles_for "$PLATFORM"))" >&2; return 1; }
}

profiles_for() { case "$1" in cuda) echo "$PROFILES_CUDA" ;; *) echo "$PROFILES_MLX" ;; esac; }

# mlx (Apple silicon), cuda (Linux with a working NVIDIA driver), or none. OPENJEV_PLATFORM overrides.
detect_platform() {
  if [ -n "${OPENJEV_PLATFORM:-}" ]; then echo "$OPENJEV_PLATFORM"; return; fi
  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64) echo mlx ;;
    Linux/*) if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then echo cuda; else echo none; fi ;;
    *) echo none ;;
  esac
}
PLATFORM="$(detect_platform)"

ram_gb() {
  if [ -r /proc/meminfo ]; then awk '/^MemTotal:/{print int($2/1048576)}' /proc/meminfo
  else echo $(( $(sysctl -n hw.memsize) / 1073741824 )); fi
}

# Memory of the first visible GPU in GB, or nothing on unified-memory GPUs (GB10 / DGX Spark,
# where nvidia-smi reports N/A) and when there is no GPU.
gpu_mem_gb() {
  local mib
  mib=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  case "$mib" in ''|*[!0-9]*) ;; *) echo $(( mib / 1024 )) ;; esac
}

# What the model has to fit in: system memory on Apple silicon and unified-memory GPUs, else GPU memory.
model_mem_gb() {
  if [ "$PLATFORM" = cuda ] && [ -n "$(gpu_mem_gb)" ]; then gpu_mem_gb; else ram_gb; fi
}

default_profile() {
  local mem; mem=$(model_mem_gb)
  if [ "$PLATFORM" = cuda ]; then
    if [ "$mem" -ge 48 ]; then echo 27b-fp8; else echo flash-fp8; fi
  elif [ "$mem" -ge 64 ]; then echo 27b-8bit
  elif [ "$mem" -ge 32 ]; then echo 27b-4bit
  else echo flash-4bit; fi
}

# Read one KEY=value from runtime/active.env (written by the installer).
active_get() { [ -f "$ACTIVE_ENV" ] && (. "$ACTIVE_ENV"; eval "echo \${$1:-}") || true; }

# The profile chosen at install time (or the memory-based default).
active_profile() {
  local p; p=$(active_get OPENJEV_PROFILE)
  echo "${p:-$(default_profile)}"
}

free_gb() { df -Pk "$1" | awk 'NR==2{print int($4/1048576)}'; }
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -c1-64; }

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
