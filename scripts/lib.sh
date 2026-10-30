# Shared settings for the OpenJev toolkit scripts. Sourced, not executed.
#
# A "profile" is one OpenJev build plus the calibration constants its model card
# publishes. The READOUT_* values must match the build: they are what turns the
# letter scores into calibrated probabilities.

TOOLKIT_HOME="${TOOLKIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RUNTIME="${OPENJEV_RUNTIME:-$TOOLKIT_HOME/runtime}"
VENV="$RUNTIME/venv"
PY="$VENV/bin/python"
MODELS_DIR="$RUNTIME/models"
HELPERS_DIR="$RUNTIME/helpers"
ACTIVE_ENV="$RUNTIME/active.env"
PID_FILE="$RUNTIME/server.pid"
LOG_FILE="$RUNTIME/server.log"

# The helper (shim.py) is frozen upstream; shim_mlx.py refuses any other version.
HELPER_SHA_PREFIX="81a22f1b"

PROFILES="27b-8bit 27b-4bit flash-8bit flash-4bit"

profile() {
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
    *) echo "unknown profile '$1' (choose from: $PROFILES)" >&2; return 1 ;;
  esac
  MODEL_DIR="$MODELS_DIR/${MODEL_REPO#openjev/}"
  HELPER_DIR="$HELPERS_DIR/${HELPER_REPO#openjev/}"
}

ram_gb() { echo $(( $(sysctl -n hw.memsize) / 1073741824 )); }

default_profile() {
  local ram; ram=$(ram_gb)
  if   [ "$ram" -ge 64 ]; then echo 27b-8bit
  elif [ "$ram" -ge 32 ]; then echo 27b-4bit
  else echo flash-4bit; fi
}

# The profile chosen at install time (or the RAM-based default).
active_profile() {
  if [ -f "$ACTIVE_ENV" ]; then (. "$ACTIVE_ENV"; echo "$OPENJEV_PROFILE"); else default_profile; fi
}

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
