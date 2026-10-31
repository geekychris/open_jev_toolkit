#!/usr/bin/env bash
# Run the playground against this machine's OpenJev API, with the settings in runtime/active.env.
#
#   scripts/playground.sh [openjev playground options]
#
# Env (or runtime/active.env): PLAYGROUND_HOST (127.0.0.1), PLAYGROUND_PORT (3020), PLAYGROUND_TOKEN,
# PLAYGROUND_DEBUG=1 (no token on the network), OPENJEV_SAMPLES. OPENJEV_URL defaults to this
# machine's API, and SHIM_TOKEN comes from the saved settings.
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"

[ -x "$VENV/bin/openjev" ] || die "no runtime; run scripts/install.sh first"
api_host="${OPENJEV_HOST:-127.0.0.1}"; [ "$api_host" = 0.0.0.0 ] && api_host=127.0.0.1
export OPENJEV_URL="${OPENJEV_URL:-http://$api_host:${OPENJEV_PORT:-3000}}"
exec "$VENV/bin/openjev" playground --no-open "$@"
