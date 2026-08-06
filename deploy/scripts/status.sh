#!/usr/bin/env sh
set -eu
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"
if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "name=$CONTAINER_NAME exists=false"
  exit 0
fi
docker inspect --format 'name={{.Name}} exists=true image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} created={{.Created}} restarts={{.RestartCount}}' "$CONTAINER_NAME"
