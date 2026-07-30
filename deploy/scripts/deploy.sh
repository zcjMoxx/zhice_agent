#!/usr/bin/env sh
set -eu

IMAGE_REF="${1:?usage: deploy.sh registry/image@sha256:digest [host-port]}"
HOST_PORT="${2:-10086}"
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"

case "$IMAGE_REF" in
  *@sha256:*) ;;
  *) echo "IMAGE_REF must be immutable and include @sha256:digest" >&2; exit 2 ;;
esac

docker pull "$IMAGE_REF"
for volume in zhice-contexts zhice-state zhice-logs zhice-extends; do
  docker volume create "$volume" >/dev/null
done

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  docker rm -f "${CONTAINER_NAME}-previous" >/dev/null 2>&1 || true
  docker stop --time 30 "$CONTAINER_NAME" >/dev/null
  docker rename "$CONTAINER_NAME" "${CONTAINER_NAME}-previous"
fi

if ! docker run -d --name "$CONTAINER_NAME" --init --restart unless-stopped \
  -p "${HOST_PORT}:10086" \
  -v zhice-contexts:/home/zhice/.zhice/contexts \
  -v zhice-state:/home/zhice/.zhice/state \
  -v zhice-logs:/home/zhice/.zhice/logs \
  -v zhice-extends:/home/zhice/.zhice/extends \
  "$IMAGE_REF" >/dev/null; then
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  if docker container inspect "${CONTAINER_NAME}-previous" >/dev/null 2>&1; then
    docker rename "${CONTAINER_NAME}-previous" "$CONTAINER_NAME"
    docker start "$CONTAINER_NAME" >/dev/null
  fi
  exit 1
fi

attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_NAME")" = "healthy" ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    docker logs --tail 100 "$CONTAINER_NAME" >&2
    docker rm -f "$CONTAINER_NAME" >/dev/null
    if docker container inspect "${CONTAINER_NAME}-previous" >/dev/null 2>&1; then
      docker rename "${CONTAINER_NAME}-previous" "$CONTAINER_NAME"
      docker start "$CONTAINER_NAME" >/dev/null
    fi
    exit 1
  fi
  sleep 2
done

docker rm "${CONTAINER_NAME}-previous" >/dev/null 2>&1 || true
echo "Deployed $IMAGE_REF"
