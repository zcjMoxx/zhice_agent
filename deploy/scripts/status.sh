#!/usr/bin/env sh
set -eu

CONTAINER_NAME=zhice-agent
DOCKER=/usr/bin/docker

if ! "$DOCKER" info >/dev/null 2>&1; then
  echo "docker_daemon=unavailable name=$CONTAINER_NAME"
  exit 1
fi
if ! "$DOCKER" container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "name=$CONTAINER_NAME exists=false"
  exit 0
fi
"$DOCKER" inspect --format 'name={{.Name}} exists=true image={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} started_at={{.State.StartedAt}} created={{.Created}} restarts={{.RestartCount}}' "$CONTAINER_NAME"
if "$DOCKER" container inspect zhice-xhs-readonly >/dev/null 2>&1; then
  "$DOCKER" inspect --format 'name={{.Name}} exists=true image={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} started_at={{.State.StartedAt}} created={{.Created}} restarts={{.RestartCount}}' zhice-xhs-readonly
else
  echo "name=zhice-xhs-readonly exists=false"
fi
