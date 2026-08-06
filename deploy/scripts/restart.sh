#!/usr/bin/env sh
set -eu

CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"

if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "Container does not exist: $CONTAINER_NAME" >&2
  exit 1
fi

docker restart --time 30 "$CONTAINER_NAME" >/dev/null
echo "Restarted $CONTAINER_NAME"
