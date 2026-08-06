#!/usr/bin/env sh
set -eu
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"
if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "Container already absent: $CONTAINER_NAME"
  exit 0
fi
if [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" != "true" ]; then
  echo "Container already stopped: $CONTAINER_NAME"
  exit 0
fi
docker stop --time 30 "$CONTAINER_NAME" >/dev/null
echo "Stopped $CONTAINER_NAME"
