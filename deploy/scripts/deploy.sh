#!/usr/bin/env sh
set -eu

IMAGE_REF="${1:?usage: deploy.sh registry/image@sha256:digest [host-port]}"
HOST_PORT="${2:-10086}"
CONTAINER_NAME="${ZHICE_CONTAINER_NAME:-zhice-agent}"
PREVIOUS_NAME="${CONTAINER_NAME}-previous"
HAS_PREVIOUS=0

rollback() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  if [ "$HAS_PREVIOUS" -eq 1 ] && docker container inspect "$PREVIOUS_NAME" >/dev/null 2>&1; then
    docker rename "$PREVIOUS_NAME" "$CONTAINER_NAME"
    docker start "$CONTAINER_NAME" >/dev/null
    echo "Deployment failed; restored previous container" >&2
  fi
}

case "$IMAGE_REF" in
  *@sha256:*) ;;
  *) echo "IMAGE_REF must be immutable and include @sha256:digest" >&2; exit 2 ;;
esac

docker pull "$IMAGE_REF"
for volume in zhice-contexts zhice-state zhice-logs zhice-extends zhice-weixin-credentials; do
  docker volume create "$volume" >/dev/null
done
docker run --rm --user root --entrypoint sh \
  -v zhice-weixin-credentials:/home/zhice/.zhice/config/channels/weixin/accounts \
  "$IMAGE_REF" -c \
  'chown -R zhice:zhice /home/zhice/.zhice/config/channels/weixin/accounts && chmod 700 /home/zhice/.zhice/config/channels/weixin/accounts'

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  docker rm -f "$PREVIOUS_NAME" >/dev/null 2>&1 || true
  docker stop --time 30 "$CONTAINER_NAME" >/dev/null
  if ! docker rename "$CONTAINER_NAME" "$PREVIOUS_NAME"; then
    docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true
    exit 1
  fi
  HAS_PREVIOUS=1
fi

if ! docker run -d --name "$CONTAINER_NAME" --init --restart unless-stopped \
  -p "127.0.0.1:${HOST_PORT}:10086" \
  -v zhice-contexts:/home/zhice/.zhice/contexts \
  -v zhice-state:/home/zhice/.zhice/state \
  -v zhice-logs:/home/zhice/.zhice/logs \
  -v zhice-extends:/home/zhice/.zhice/extends \
  -v zhice-weixin-credentials:/home/zhice/.zhice/config/channels/weixin/accounts \
  "$IMAGE_REF" >/dev/null; then
  rollback
  exit 1
fi

attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_NAME")" = "healthy" ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    docker logs --tail 100 "$CONTAINER_NAME" >&2
    rollback
    exit 1
  fi
  sleep 2
done

docker rm "$PREVIOUS_NAME" >/dev/null 2>&1 || true
echo "Deployed $IMAGE_REF"
