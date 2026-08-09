#!/usr/bin/env sh
set -eu

SPEC=/etc/zhice-agent/deployment.spec
[ -f "$SPEC" ] && [ ! -L "$SPEC" ] || { echo "Fixed deployment specification is unavailable" >&2; exit 1; }
[ "$(wc -l < "$SPEC")" -eq 4 ] || { echo "Fixed deployment specification is invalid" >&2; exit 1; }
IMAGE_REF=$(sed -n '1p' "$SPEC")
HOST_PORT=$(sed -n '2p' "$SPEC")
PUBLIC_URL=$(sed -n '3p' "$SPEC")
OPS_URL=$(sed -n '4p' "$SPEC")

exec /bin/sh /usr/local/libexec/zhice-ops/scripts/deploy.sh \
  "$IMAGE_REF" "$HOST_PORT" "$PUBLIC_URL" "$OPS_URL"
