#!/usr/bin/env sh
set -eu

IMAGE_REF="${1:?usage: deploy.sh registry/image@sha256:digest [host-port] public-url ops-url}"
HOST_PORT="${2:-10086}"
PUBLIC_URL="${3:?public-url is required}"
OPS_URL="${4:?ops-url is required}"
CONTAINER_NAME=zhice-agent
PREVIOUS_NAME=${CONTAINER_NAME}-previous
XHS_CONTAINER_NAME=zhice-xhs-readonly
XHS_PREVIOUS_NAME=${XHS_CONTAINER_NAME}-previous
TRAVEL_NETWORK=zhice-travel
XHS_DATA_VOLUME=zhice-xhs-data
XHS_CACHE_VOLUME=zhice-xhs-cache
TRAVEL_DATA_VOLUME=zhice-travel-data
RUNTIME_PARENT=/etc/zhice-agent
RUNTIME_DIR=$RUNTIME_PARENT/runtime
XHS_SEED_DIR=$RUNTIME_PARENT/xhs
XHS_SEED_FILE=$XHS_SEED_DIR/cookies.json
HAS_PREVIOUS=0
XHS_HAS_PREVIOUS=0
INIT_DIR=
SEED_CONTAINER=
XHS_SEED_CONTAINER=

cleanup_seed() {
  if [ -n "$SEED_CONTAINER" ]; then
    /usr/bin/docker rm -f "$SEED_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "$XHS_SEED_CONTAINER" ]; then
    /usr/bin/docker rm -f "$XHS_SEED_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "$INIT_DIR" ] && [ -d "$INIT_DIR" ] && [ ! -L "$INIT_DIR" ]; then
    case "$INIT_DIR" in
      /etc/zhice-agent/.runtime-init.*) rm -rf -- "$INIT_DIR" ;;
      *) echo "Refusing unsafe runtime init cleanup target" >&2 ;;
    esac
  fi
}

rollback() {
  /usr/bin/docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  if [ "$HAS_PREVIOUS" -eq 1 ] && /usr/bin/docker container inspect "$PREVIOUS_NAME" >/dev/null 2>&1; then
    /usr/bin/docker rename "$PREVIOUS_NAME" "$CONTAINER_NAME"
    /usr/bin/docker start "$CONTAINER_NAME" >/dev/null
    echo "Deployment failed; restored previous container" >&2
  fi
}

rollback_xhs() {
  /usr/bin/docker rm -f "$XHS_CONTAINER_NAME" >/dev/null 2>&1 || true
  if [ "$XHS_HAS_PREVIOUS" -eq 1 ] && /usr/bin/docker container inspect "$XHS_PREVIOUS_NAME" >/dev/null 2>&1; then
    /usr/bin/docker rename "$XHS_PREVIOUS_NAME" "$XHS_CONTAINER_NAME"
    /usr/bin/docker start "$XHS_CONTAINER_NAME" >/dev/null
    echo "Xiaohongshu sidecar update failed; restored previous container" >&2
  fi
}

validate_runtime_dir() {
  validate_dir=$1
  /usr/bin/docker run --rm --user root --entrypoint python \
    --mount "type=bind,src=$validate_dir,dst=/runtime,readonly" \
    "$IMAGE_REF" -c '
import json, pathlib, re, yaml
root = pathlib.Path("/runtime")
env = root / ".env"
cfg = root / "config.yml"
models = root / "models.json"
for path in (env, cfg, models):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 262144:
        raise SystemExit("runtime configuration validation failed")
env_keys = set()
for number, raw in enumerate(env.read_text(encoding="utf-8").splitlines(), 1):
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", line.split("=", 1)[0].strip()):
        raise SystemExit(f"invalid .env syntax at line {number}")
    key = line.split("=", 1)[0].strip()
    if key in env_keys or key == "ZHICE_AGENT_WORKSPACE":
        raise SystemExit("invalid or duplicate .env key")
    env_keys.add(key)
config = yaml.safe_load(cfg.read_text(encoding="utf-8"))
if not isinstance(config, dict) or config.get("schema_version") != 1:
    raise SystemExit("invalid config.yml schema")
for section in ("context", "skills", "subagents", "channels", "hooks", "mcp", "logging"):
    if section in config and not isinstance(config[section], dict):
        raise SystemExit(f"invalid config.yml {section} section")
model_config = json.loads(models.read_text(encoding="utf-8"))
if not isinstance(model_config, dict) or model_config.get("schema_version") != 1:
    raise SystemExit("invalid models.json schema")
if not isinstance(model_config.get("routing"), dict) or not isinstance(model_config.get("chat"), dict):
    raise SystemExit("invalid models.json routing/chat")
' >/dev/null
}

initialize_runtime_config() {
  if [ -e "$RUNTIME_DIR" ]; then
    [ ! -L "$RUNTIME_DIR" ] || { echo "Runtime config directory must not be a symlink" >&2; exit 1; }
    for name in .env config.yml models.json; do
      [ -f "$RUNTIME_DIR/$name" ] && [ ! -L "$RUNTIME_DIR/$name" ] || {
        echo "Runtime config is incomplete; refusing mixed initialization" >&2
        exit 1
      }
    done
    validate_runtime_dir "$RUNTIME_DIR"
    return
  fi

  install -d -o root -g root -m 0700 "$RUNTIME_PARENT"
  INIT_DIR=$(mktemp -d "$RUNTIME_PARENT/.runtime-init.XXXXXX")
  SEED_CONTAINER="zhice-config-seed-$$"
  /usr/bin/docker create --name "$SEED_CONTAINER" "$IMAGE_REF" >/dev/null
  for name in .env config.yml models.json; do
    /usr/bin/docker cp \
      "$SEED_CONTAINER:/home/zhice/.zhice/config/$name" "$INIT_DIR/$name"
  done
  /usr/bin/docker rm "$SEED_CONTAINER" >/dev/null
  SEED_CONTAINER=

  install -d -o root -g root -m 0700 "$INIT_DIR/backups"
  validate_runtime_dir "$INIT_DIR"
  mv "$INIT_DIR" "$RUNTIME_DIR"
  INIT_DIR=
  echo "Initialized host runtime configuration without displaying its contents"
}

trap cleanup_seed EXIT HUP INT TERM

case "$IMAGE_REF" in
  *@sha256:*) ;;
  *) echo "IMAGE_REF must be immutable and include @sha256:digest" >&2; exit 2 ;;
esac
case "$HOST_PORT" in
  ''|*[!0-9]*|0) echo "host-port must be a positive integer" >&2; exit 2 ;;
esac
case "$PUBLIC_URL" in
  https://*/*) echo "public-url must be an HTTPS origin without a path" >&2; exit 2 ;;
  https://*) ;;
  *) echo "public-url must be an HTTPS origin" >&2; exit 2 ;;
esac
case "$OPS_URL" in
  https://*/*) echo "ops-url must be an HTTPS origin without a path" >&2; exit 2 ;;
  https://*) ;;
  *) echo "ops-url must be an HTTPS origin" >&2; exit 2 ;;
esac
if [ "$HOST_PORT" -gt 65535 ]; then
  echo "host-port must not exceed 65535" >&2
  exit 2
fi

/usr/bin/docker pull "$IMAGE_REF"
IMAGE_IDS=$(/usr/bin/docker run --rm --entrypoint sh "$IMAGE_REF" -c 'printf "%s:%s" "$(id -u zhice)" "$(id -g zhice)"')
case "$IMAGE_IDS" in
  *:*:*) echo "Invalid image runtime uid/gid" >&2; exit 1 ;;
  *:*) ;;
  *) echo "Invalid image runtime uid/gid" >&2; exit 1 ;;
esac
IMAGE_UID=${IMAGE_IDS%%:*}
IMAGE_GID=${IMAGE_IDS#*:}
[ -n "$IMAGE_UID" ] && [ -n "$IMAGE_GID" ] || {
  echo "Invalid image runtime uid/gid" >&2
  exit 1
}
case "$IMAGE_UID$IMAGE_GID" in
  *[!0-9]*) echo "Invalid image runtime uid/gid" >&2; exit 1 ;;
esac
initialize_runtime_config
chown "$IMAGE_UID:$IMAGE_GID" \
  "$RUNTIME_DIR/.env" "$RUNTIME_DIR/config.yml" "$RUNTIME_DIR/models.json"
chmod 0400 "$RUNTIME_DIR/.env" "$RUNTIME_DIR/config.yml" "$RUNTIME_DIR/models.json"
for volume in zhice-contexts zhice-state zhice-logs zhice-extends zhice-weixin-credentials "$TRAVEL_DATA_VOLUME" "$XHS_DATA_VOLUME" "$XHS_CACHE_VOLUME"; do
  /usr/bin/docker volume create "$volume" >/dev/null
done
/usr/bin/docker network inspect "$TRAVEL_NETWORK" >/dev/null 2>&1 || \
  /usr/bin/docker network create "$TRAVEL_NETWORK" >/dev/null
/usr/bin/docker run --rm --user root --entrypoint sh \
  -v zhice-weixin-credentials:/home/zhice/.zhice/config/channels/weixin/accounts \
  "$IMAGE_REF" -c \
  'chown -R zhice:zhice /home/zhice/.zhice/config/channels/weixin/accounts && chmod 700 /home/zhice/.zhice/config/channels/weixin/accounts'

XHS_COOKIE_PRESENT=$(/usr/bin/docker run --rm --entrypoint sh \
  -v "$XHS_DATA_VOLUME":/xhs-data "$IMAGE_REF" -c \
  'if [ -s /xhs-data/cookies.json ]; then printf yes; else printf no; fi')
if [ "$XHS_COOKIE_PRESENT" = "no" ] && [ -s "$XHS_SEED_FILE" ] && [ ! -L "$XHS_SEED_FILE" ]; then
  XHS_SEED_CONTAINER="zhice-xhs-seed-$$"
  /usr/bin/docker create --name "$XHS_SEED_CONTAINER" \
    -v "$XHS_DATA_VOLUME":/home/zhice/.zhice/integrations/xhs/data "$IMAGE_REF" >/dev/null
  /usr/bin/docker cp "$XHS_SEED_FILE" \
    "$XHS_SEED_CONTAINER:/home/zhice/.zhice/integrations/xhs/data/cookies.json"
  /usr/bin/docker rm "$XHS_SEED_CONTAINER" >/dev/null
  XHS_SEED_CONTAINER=
fi
/usr/bin/docker run --rm --user root --entrypoint sh \
  -v "$XHS_DATA_VOLUME":/home/zhice/.zhice/integrations/xhs/data \
  -v "$XHS_CACHE_VOLUME":/home/zhice/.cache/xiaohongshu-mcp \
  "$IMAGE_REF" -c \
  'chown -R zhice:zhice /home/zhice/.zhice/integrations/xhs/data /home/zhice/.cache/xiaohongshu-mcp && chmod 700 /home/zhice/.zhice/integrations/xhs/data /home/zhice/.cache/xiaohongshu-mcp && if [ -f /home/zhice/.zhice/integrations/xhs/data/cookies.json ]; then chmod 600 /home/zhice/.zhice/integrations/xhs/data/cookies.json; fi'

if /usr/bin/docker container inspect "$XHS_CONTAINER_NAME" >/dev/null 2>&1; then
  /usr/bin/docker rm -f "$XHS_PREVIOUS_NAME" >/dev/null 2>&1 || true
  /usr/bin/docker stop --time 30 "$XHS_CONTAINER_NAME" >/dev/null
  if ! /usr/bin/docker rename "$XHS_CONTAINER_NAME" "$XHS_PREVIOUS_NAME"; then
    /usr/bin/docker start "$XHS_CONTAINER_NAME" >/dev/null 2>&1 || true
    exit 1
  fi
  XHS_HAS_PREVIOUS=1
fi
if ! /usr/bin/docker run -d --name "$XHS_CONTAINER_NAME" --init --restart unless-stopped \
  --network "$TRAVEL_NETWORK" \
  --security-opt no-new-privileges:true --cap-drop ALL \
  -e COOKIES_PATH=/home/zhice/.zhice/integrations/xhs/data/cookies.json \
  -v "$XHS_DATA_VOLUME":/home/zhice/.zhice/integrations/xhs/data \
  -v "$XHS_CACHE_VOLUME":/home/zhice/.cache/xiaohongshu-mcp \
  --entrypoint /opt/zhice/bin/xiaohongshu-mcp-rednote \
  "$IMAGE_REF" -headless=true -port=:18060 >/dev/null; then
  echo "Xiaohongshu sidecar failed to start" >&2
  rollback_xhs
  exit 1
fi
attempt=0
until /usr/bin/docker exec "$XHS_CONTAINER_NAME" python -c \
  "import socket; socket.create_connection(('127.0.0.1', 18060), 3).close()" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 90 ]; then
    echo "Xiaohongshu sidecar failed readiness verification" >&2
    rollback_xhs
    exit 1
  fi
  sleep 2
done

if /usr/bin/docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  /usr/bin/docker rm -f "$PREVIOUS_NAME" >/dev/null 2>&1 || true
  /usr/bin/docker stop --time 30 "$CONTAINER_NAME" >/dev/null
  if ! /usr/bin/docker rename "$CONTAINER_NAME" "$PREVIOUS_NAME"; then
    /usr/bin/docker start "$CONTAINER_NAME" >/dev/null 2>&1 || true
    exit 1
  fi
  HAS_PREVIOUS=1
fi

if ! /usr/bin/docker run -d --name "$CONTAINER_NAME" --init --restart unless-stopped \
  --network "$TRAVEL_NETWORK" \
  -e ZHICE_OPS_MODE=server_docker \
  -e ZHICE_OPS_URL="$OPS_URL" \
  -e ZHICE_OPS_TARGET_TYPE=container \
  -e ZHICE_OPS_TARGET_NAME=zhice-agent \
  -e XHS_READONLY_UPSTREAM_URL=http://zhice-xhs-readonly:18060/mcp \
  -e XHS_READONLY_HTTP_HOST_ALLOWLIST=zhice-xhs-readonly \
  -e XHS_READONLY_COOKIE_DIR=/home/zhice/.zhice/integrations/xhs/data \
  -e XHS_READONLY_COOKIE_FILE=/home/zhice/.zhice/integrations/xhs/data/cookies.json \
  -e HOTEL_BROWSER_CHANNEL= \
  -p "127.0.0.1:${HOST_PORT}:10086" \
  -v zhice-contexts:/home/zhice/.zhice/contexts \
  -v zhice-state:/home/zhice/.zhice/state \
  -v "$TRAVEL_DATA_VOLUME":/home/zhice/.zhice/travel \
  -v zhice-logs:/home/zhice/.zhice/logs \
  -v zhice-extends:/home/zhice/.zhice/extends \
  -v zhice-weixin-credentials:/home/zhice/.zhice/config/channels/weixin/accounts \
  -v "$XHS_DATA_VOLUME":/home/zhice/.zhice/integrations/xhs/data:ro \
  --mount "type=bind,src=$RUNTIME_DIR/.env,dst=/home/zhice/.zhice/config/.env,readonly" \
  --mount "type=bind,src=$RUNTIME_DIR/config.yml,dst=/home/zhice/.zhice/config/config.yml,readonly" \
  --mount "type=bind,src=$RUNTIME_DIR/models.json,dst=/home/zhice/.zhice/config/models.json,readonly" \
  "$IMAGE_REF" >/dev/null; then
  rollback
  rollback_xhs
  exit 1
fi

attempt=0
until [ "$(/usr/bin/docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_NAME")" = "healthy" ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "New container failed health verification; logs remain available through restricted Ops" >&2
    rollback
    rollback_xhs
    exit 1
  fi
  sleep 2
done

/usr/bin/docker rm "$PREVIOUS_NAME" >/dev/null 2>&1 || true
/usr/bin/docker rm "$XHS_PREVIOUS_NAME" >/dev/null 2>&1 || true
umask 077
printf '%s\n%s\n%s\n%s\n' "$IMAGE_REF" "$HOST_PORT" "$PUBLIC_URL" "$OPS_URL" > "$RUNTIME_PARENT/deployment.spec.tmp"
mv "$RUNTIME_PARENT/deployment.spec.tmp" "$RUNTIME_PARENT/deployment.spec"
echo "Deployed $IMAGE_REF with host-authoritative read-only runtime configuration"
