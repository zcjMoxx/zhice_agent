#!/usr/bin/env sh
set -u

CONTAINER_NAME=zhice-agent
DOCKER=/usr/bin/docker
PUBLIC_HEALTH_URL=${ZHICE_PUBLIC_HEALTH_URL:-}
LOCAL_HEALTH_URL=http://127.0.0.1:10086/health

echo "section=host"
echo "hostname=$(hostname 2>/dev/null || echo unavailable)"
echo "uptime=$(uptime -p 2>/dev/null || echo unavailable)"
echo "load=$(cut -d ' ' -f 1-3 /proc/loadavg 2>/dev/null || echo unavailable)"
df -h / /etc/zhice-agent/runtime 2>/dev/null | head -n 5 || true
free -h 2>/dev/null | head -n 3 || true

echo "section=docker"
if ! "$DOCKER" info >/dev/null 2>&1; then
  echo "docker_daemon=unavailable"
  DOCKER_AVAILABLE=0
else
  echo "docker_daemon=available"
  DOCKER_AVAILABLE=1
fi

echo "section=container"
if [ "$DOCKER_AVAILABLE" -eq 0 ]; then
  echo "name=$CONTAINER_NAME status=unknown reason=docker-unavailable"
elif ! "$DOCKER" container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "name=$CONTAINER_NAME exists=false"
else
  "$DOCKER" inspect --format 'name={{.Name}} exists=true image={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} started_at={{.State.StartedAt}} restarts={{.RestartCount}}' "$CONTAINER_NAME" 2>/dev/null || true
  echo "section=bounded_logs tail=50"
  "$DOCKER" logs --tail 50 "$CONTAINER_NAME" 2>&1 | head -c 65536 || true
  echo
fi

echo "section=volumes"
for volume in zhice-contexts zhice-state zhice-logs zhice-extends zhice-weixin-credentials; do
  if [ "$DOCKER_AVAILABLE" -eq 0 ]; then
    echo "volume=$volume exists=unknown reason=docker-unavailable"
  elif "$DOCKER" volume inspect "$volume" >/dev/null 2>&1; then
    echo "volume=$volume exists=true"
  else
    echo "volume=$volume exists=false"
  fi
done

echo "section=host_port port=10086"
if command -v ss >/dev/null 2>&1; then
  ss -ltn 2>/dev/null | awk '$4 ~ /:10086$/ {print}' | head -n 5 || true
else
  echo "ss=unavailable"
fi

echo "section=health"
if command -v curl >/dev/null 2>&1; then
  if curl --fail --silent --show-error --max-time 5 -- "$LOCAL_HEALTH_URL" >/dev/null 2>&1; then
    echo "local_health=ok"
  else
    echo "local_health=failed"
  fi
  if [ -z "$PUBLIC_HEALTH_URL" ]; then
    echo "public_health=not-configured"
  elif curl --fail --silent --show-error --max-time 20 -- "$PUBLIC_HEALTH_URL" >/dev/null 2>&1; then
    echo "public_health=ok url=$PUBLIC_HEALTH_URL"
  else
    echo "public_health=failed url=$PUBLIC_HEALTH_URL"
  fi
else
  echo "curl=unavailable"
fi
