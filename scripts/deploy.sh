#!/usr/bin/env bash
# Deploy the toolkit to another machine over SSH and run it there as a service.
#
#   scripts/deploy.sh USER@HOST [options]
#
# On the host it clones (or updates) the repo from GitHub, runs scripts/install.sh with your options,
# and installs the systemd user service (scripts/service.sh), then checks the API from here.
# The host needs git, curl and ssh access; uv is installed if missing. Deploys what is pushed to
# GitHub, not local changes.
#
# Options:
#   --profile P     model profile (default: chosen by the host's memory)
#   --port N        API port (default 3000)
#   --listen ADDR   bind address (default 127.0.0.1; 0.0.0.0 to serve the LAN, with a generated token)
#   --ref REF       branch to deploy (default main)
#   --dir DIR       checkout location on the host (default ~/open_jev_toolkit)
#   --vllm-image I  NVIDIA: run vLLM from a Docker image
#   --no-service    install only; start it yourself with scripts/serve.sh start
#
# Example:
#   scripts/deploy.sh chris@spark.local --profile 27b-fp8 --port 3030 --listen 0.0.0.0
set -euo pipefail

[ $# -ge 1 ] && [[ "$1" != -* ]] || { sed -n '2,22p' "$0"; exit 1; }
TARGET="$1"; shift
REPO_URL="${OPENJEV_REPO_URL:-https://github.com/geekychris/open_jev_toolkit.git}"
# ~ expands on the remote host, not here.
# shellcheck disable=SC2088
DIR='~/open_jev_toolkit'
REF=main; SERVICE=1; INSTALL_ARGS=(); PORT=3000; LISTEN=127.0.0.1
while [ $# -gt 0 ]; do
  case "$1" in
    --profile|--vllm-image) INSTALL_ARGS+=("$1" "$2"); shift 2 ;;
    --port) PORT="$2"; INSTALL_ARGS+=(--port "$2"); shift 2 ;;
    --listen) LISTEN="$2"; INSTALL_ARGS+=(--listen "$2"); shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --no-service) SERVICE=0; shift ;;
    *) echo "unknown option $1" >&2; exit 1 ;;
  esac
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
say "deploying $REPO_URL@$REF to $TARGET:$DIR"

# The remote side, as one script: arguments are passed as positional parameters, not interpolated.
ssh -o BatchMode=yes "$TARGET" bash -s -- "$REPO_URL" "$REF" "$DIR" "$SERVICE" "${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}" <<'REMOTE'
set -euo pipefail
REPO_URL="$1"; REF="$2"; DIR="${3/#\~/$HOME}"; SERVICE="$4"; shift 4
export PATH="$HOME/.local/bin:$PATH"
if [ -d "$DIR/.git" ]; then
  echo "==> updating $DIR to $REF"
  git -C "$DIR" fetch -q origin
  git -C "$DIR" checkout -q "$REF"
  git -C "$DIR" merge -q --ff-only "origin/$REF"
else
  echo "==> cloning into $DIR"
  git clone -q -b "$REF" "$REPO_URL" "$DIR"
fi
echo "==> $(git -C "$DIR" log --oneline -1)"
"$DIR/scripts/install.sh" --no-check "$@"
if [ "$SERVICE" = 1 ]; then "$DIR/scripts/service.sh" install; fi
REMOTE

[ "$SERVICE" = 1 ] || { say "installed; on the host run: $DIR/scripts/serve.sh start"; exit 0; }

HOST="${TARGET#*@}"
if [ "$LISTEN" = 127.0.0.1 ] || [ "$LISTEN" = localhost ]; then
  say "running on $HOST, localhost only. From here, tunnel it:"
  echo "    ssh -N -L $PORT:127.0.0.1:$PORT $TARGET &"
  echo "    OPENJEV_URL=http://127.0.0.1:$PORT runtime/venv/bin/openjev version"
  exit 0
fi

# The helper listens on IPv4 only. A name that also resolves to IPv6 (spark.local does) makes Python
# clients try the IPv6 addresses first and wait for each refusal, about a second apiece, on every
# request. So hand out the host's IPv4 address when it answers.
ADDR=$(ssh -o BatchMode=yes "$TARGET" "hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+(\.[0-9]+){3}\$' | head -1" || true)

# Check the API across the network with the token, without printing the token.
TOKEN=$(ssh -o BatchMode=yes "$TARGET" "grep '^SHIM_TOKEN=' $DIR/runtime/active.env | cut -d= -f2-")
URL=""
for h in ${ADDR:+"$ADDR"} "$HOST"; do
  if curl -fs -m 10 -H "Authorization: Bearer $TOKEN" "http://$h:$PORT/v1/version" >/dev/null; then URL="http://$h:$PORT"; break; fi
done
if [ -n "$URL" ]; then
  say "reachable from here: $URL/v1/systemone (bearer token required)"
  [ "$URL" = "http://$HOST:$PORT" ] || say "(http://$HOST:$PORT works too, but if $HOST resolves to IPv6 as well, Python clients pay ~1 s per IPv6 address on every request)"
else
  URL="http://$HOST:$PORT"
  say "the service is up on $HOST, but $URL is not reachable from here (firewall?)"
fi
cat <<EOF

Use it from this machine:
    export OPENJEV_URL=$URL
    export SHIM_TOKEN=\$(ssh $TARGET "grep '^SHIM_TOKEN=' $DIR/runtime/active.env | cut -d= -f2-")
    runtime/venv/bin/openjev version

On the host: $DIR/scripts/service.sh status | logs | restart | stop
EOF
