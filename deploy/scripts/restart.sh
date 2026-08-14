#!/usr/bin/env sh
set -eu

CONTAINER_NAME=zhice-agent
XHS_CONTAINER_NAME=zhice-xhs-readonly
DOCKER=/usr/bin/docker

for name in "$XHS_CONTAINER_NAME" "$CONTAINER_NAME"; do
  if ! "$DOCKER" container inspect "$name" >/dev/null 2>&1; then
    echo "Container does not exist: $name" >&2
    exit 1
  fi
done

"$DOCKER" restart --time 30 "$XHS_CONTAINER_NAME" >/dev/null
attempt=0
until "$DOCKER" exec "$XHS_CONTAINER_NAME" python -c \
  "import socket; socket.create_connection(('127.0.0.1', 18060), 3).close()" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 90 ]; then
    echo "Restart readiness verification failed: $XHS_CONTAINER_NAME" >&2
    exit 1
  fi
  sleep 2
done

"$DOCKER" restart --time 30 "$CONTAINER_NAME" >/dev/null
attempt=0
while :; do
  status=$("$DOCKER" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME")
  if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
    echo "Restarted $XHS_CONTAINER_NAME and $CONTAINER_NAME health=$status"
    exit 0
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "Restart health verification failed: status=$status" >&2
    exit 1
  fi
  sleep 2
done
