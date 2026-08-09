#!/usr/bin/env sh
set -eu

CONTAINER_NAME=zhice-agent
DOCKER=/usr/bin/docker

if ! "$DOCKER" container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "Container does not exist: $CONTAINER_NAME" >&2
  exit 1
fi

"$DOCKER" restart --time 30 "$CONTAINER_NAME" >/dev/null
attempt=0
while :; do
  status=$("$DOCKER" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME")
  if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
    echo "Restarted $CONTAINER_NAME health=$status"
    exit 0
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "Restart health verification failed: status=$status" >&2
    exit 1
  fi
  sleep 2
done
