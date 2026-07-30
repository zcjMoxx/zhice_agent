#!/usr/bin/env sh
set -eu
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"
docker inspect --format 'name={{.Name}} image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER_NAME"
