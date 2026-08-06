#!/usr/bin/env sh
set -eu
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"
LINES="${1:-200}"
case "$LINES" in
  ''|*[!0-9]*|0) echo "LINES must be a positive integer" >&2; exit 2 ;;
esac
docker logs --tail "$LINES" "$CONTAINER_NAME"
