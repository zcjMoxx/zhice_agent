#!/usr/bin/env sh
set -eu
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"
docker stop --time 30 "$CONTAINER_NAME"
