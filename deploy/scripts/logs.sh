#!/usr/bin/env sh
set -eu

CONTAINER_NAME=zhice-agent
DOCKER=/usr/bin/docker
MAX_LINES=500

if [ "${1:-}" = "--follow" ]; then
  [ "$#" -eq 1 ] || { echo "logs-follow does not accept arguments" >&2; exit 2; }
  exec "$DOCKER" logs --tail 100 --follow "$CONTAINER_NAME"
fi

[ "$#" -le 1 ] || { echo "logs accepts at most one line count" >&2; exit 2; }
LINES="${1:-200}"
case "$LINES" in
  ''|*[!0-9]*|0) echo "LINES must be a positive integer" >&2; exit 2 ;;
esac
if [ "$LINES" -gt "$MAX_LINES" ]; then
  echo "LINES must not exceed $MAX_LINES" >&2
  exit 2
fi
"$DOCKER" logs --tail "$LINES" "$CONTAINER_NAME"
