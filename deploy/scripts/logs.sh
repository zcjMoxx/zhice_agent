#!/usr/bin/env sh
set -eu
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"
LINES="${1:-200}"
docker logs --tail "$LINES" "$CONTAINER_NAME"
