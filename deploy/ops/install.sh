#!/usr/bin/env sh
set -eu

PUBLIC_URL="${1:?usage: install.sh public-url ops-url}"
OPS_URL="${2:?usage: install.sh public-url ops-url}"

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh must run as root" >&2
  exit 1
fi

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -d "$SOURCE_DIR/../scripts" ]; then
  SCRIPTS_SOURCE=$(CDPATH= cd -- "$SOURCE_DIR/../scripts" && pwd)
else
  SCRIPTS_SOURCE=$(CDPATH= cd -- "$SOURCE_DIR/.." && pwd)
fi
VERSION_FILE="$SOURCE_DIR/ttyd-version.env"

if [ ! -f "$VERSION_FILE" ]; then
  echo "Missing ttyd version manifest" >&2
  exit 1
fi
# The manifest is repository-owned and contains only four fixed scalar values.
# shellcheck disable=SC1090
. "$VERSION_FILE"

case "${TTYD_VERSION:-}" in
  1.7.7) ;;
  *) echo "Unexpected ttyd version" >&2; exit 1 ;;
esac
case "${TTYD_ARCH:-}" in
  x86_64) ;;
  *) echo "Unsupported ttyd architecture" >&2; exit 1 ;;
esac
case "${TTYD_SHA256:-}" in
  [0-9a-f][0-9a-f]*) [ "${#TTYD_SHA256}" -eq 64 ] || exit 1 ;;
  *) echo "Invalid ttyd checksum" >&2; exit 1 ;;
esac

machine=$(uname -m)
if [ "$machine" != "$TTYD_ARCH" ]; then
  echo "ttyd asset only supports x86_64; found $machine" >&2
  exit 1
fi

for command in base64 caddy cp curl sha256sum install od tr useradd systemctl; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Required command is unavailable: $command" >&2
    exit 1
  fi
done
if [ ! -x /usr/bin/python3 ] || ! /usr/bin/python3 -c 'import yaml' >/dev/null 2>&1; then
  echo "Required host runtime is unavailable: /usr/bin/python3 with PyYAML (python3-yaml)" >&2
  exit 1
fi

if ! id zhice-operator >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/zhice-ops --create-home \
    --shell /usr/sbin/nologin zhice-operator
fi
if command -v gpasswd >/dev/null 2>&1; then
  gpasswd -d zhice-operator docker >/dev/null 2>&1 || true
fi

install -d -o root -g root -m 0755 /opt/zhice-ops/bin
install -d -o root -g root -m 0755 /usr/local/libexec/zhice-ops/scripts
install -d -o root -g root -m 0755 /usr/local/share/zhice-ops
install -d -o root -g root -m 0755 /etc/zhice-ops
install -d -o root -g root -m 0755 /etc/systemd/system
install -d -o root -g root -m 0755 /etc/sudoers.d
install -d -o root -g zhice-operator -m 0750 /var/lib/zhice-ops
install -d -o zhice-operator -g zhice-operator -m 0700 /var/lib/zhice-ops/caddy

temp_ttyd=$(mktemp /tmp/zhice-ttyd.XXXXXX)
cleanup() {
  rm -f -- "$temp_ttyd"
}
trap cleanup EXIT HUP INT TERM
if [ -f /opt/zhice-ops/bin/ttyd ] && \
  printf '%s  %s\n' "$TTYD_SHA256" /opt/zhice-ops/bin/ttyd | sha256sum --check --status; then
  :
else
  curl --fail --location --silent --show-error --max-time 120 \
    --output "$temp_ttyd" -- "$TTYD_URL"
  printf '%s  %s\n' "$TTYD_SHA256" "$temp_ttyd" | sha256sum --check --status
  install -o root -g root -m 0755 "$temp_ttyd" /opt/zhice-ops/bin/ttyd
fi

install -o root -g root -m 0755 \
  "$SOURCE_DIR/bin/zhice-ops-shell" /usr/local/bin/zhice-ops-shell
install -o root -g root -m 0755 \
  "$SOURCE_DIR/libexec/zhice_ops_root.py" \
  /usr/local/libexec/zhice-ops/zhice_ops_root.py
install -o root -g root -m 0755 \
  "$SOURCE_DIR/libexec/zhice_ops_dashboard.py" \
  /usr/local/libexec/zhice-ops/zhice_ops_dashboard.py
ui_source="$SOURCE_DIR/web/index.html"
if [ ! -f "$ui_source" ]; then
  ui_source="$SOURCE_DIR/../../agent/operations/static/ops.html"
fi
if [ ! -f "$ui_source" ]; then
  echo "Shared Ops page is unavailable" >&2
  exit 1
fi
install -o root -g root -m 0644 "$ui_source" /usr/local/share/zhice-ops/index.html
install -o root -g root -m 0644 "$SOURCE_DIR/config/Caddyfile" /etc/zhice-ops/Caddyfile
for script in status.sh logs.sh restart.sh diagnose.sh deploy.sh apply.sh; do
  install -o root -g root -m 0755 \
    "$SCRIPTS_SOURCE/$script" "/usr/local/libexec/zhice-ops/scripts/$script"
done
install -o root -g root -m 0644 \
  "$SOURCE_DIR/systemd/zhice-ops.service" /etc/systemd/system/zhice-ops.service
install -o root -g root -m 0644 \
  "$SOURCE_DIR/systemd/zhice-ops-dashboard.service" \
  /etc/systemd/system/zhice-ops-dashboard.service
install -o root -g root -m 0644 \
  "$SOURCE_DIR/systemd/zhice-ops-terminal.service" \
  /etc/systemd/system/zhice-ops-terminal.service
sudoers_backup=$(mktemp /tmp/zhice-sudoers.XXXXXX)
sudoers_existed=false
if [ -f /etc/sudoers.d/zhice-ops ] && [ ! -L /etc/sudoers.d/zhice-ops ]; then
  cp -p /etc/sudoers.d/zhice-ops "$sudoers_backup"
  sudoers_existed=true
fi
install -o root -g root -m 0440 \
  "$SOURCE_DIR/sudoers.d/zhice-ops" /etc/sudoers.d/zhice-ops

if command -v visudo >/dev/null 2>&1 && ! visudo -cf /etc/sudoers >/dev/null; then
  if [ "$sudoers_existed" = true ]; then
    install -o root -g root -m 0440 "$sudoers_backup" /etc/sudoers.d/zhice-ops
  else
    rm -f /etc/sudoers.d/zhice-ops
  fi
  rm -f "$sudoers_backup"
  echo "Installed sudoers policy failed full configuration validation; previous policy restored" >&2
  exit 1
fi
rm -f "$sudoers_backup"
ops_credential=""
if [ -f /etc/zhice-ops/ops.env ] && [ ! -L /etc/zhice-ops/ops.env ]; then
  while IFS= read -r line; do
    case "$line" in
      ZHICE_OPS_CREDENTIAL=*)
        ops_credential=${line#ZHICE_OPS_CREDENTIAL=}
        break
        ;;
    esac
  done < /etc/zhice-ops/ops.env
fi
credential_secret=${ops_credential#owner:}
case "$credential_secret" in
  *[!0-9a-f]*|"") ops_credential="" ;;
esac
if [ "${#credential_secret}" -ne 48 ] || [ "$ops_credential" != "owner:$credential_secret" ]; then
  credential_secret=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
  if [ "${#credential_secret}" -ne 48 ]; then
    echo "Unable to generate Ops credential" >&2
    exit 1
  fi
  ops_credential="owner:$credential_secret"
fi
ops_basic_auth=$(printf 'owner:%s' "$credential_secret" | base64 | tr -d '\n')
case "$ops_basic_auth" in
  *[!A-Za-z0-9+/=]*|"") echo "Unable to encode Ops terminal credential" >&2; exit 1 ;;
esac

ops_env=$(mktemp /etc/zhice-ops/.ops.env.XXXXXX)
printf '%s\n' \
  'ZHICE_OPS_PORT=7681' \
  'ZHICE_OPS_TERMINAL_PORT=7682' \
  'ZHICE_OPS_DASHBOARD_PORT=7683' \
  'ZHICE_OPS_IDLE_TIMEOUT_SECONDS=900' \
  "ZHICE_OPS_CREDENTIAL=$ops_credential" \
  "ZHICE_OPS_BASIC_AUTH=$ops_basic_auth" \
  "ZHICE_OPS_ALLOWED_ORIGIN=$PUBLIC_URL" \
  "ZHICE_PUBLIC_URL=$PUBLIC_URL" \
  "ZHICE_PUBLIC_HEALTH_URL=$PUBLIC_URL/health" \
  "ZHICE_OPS_PUBLIC_URL=$OPS_URL" > "$ops_env"
chmod 0600 "$ops_env"
chown root:root "$ops_env"
mv "$ops_env" /etc/zhice-ops/ops.env

ZHICE_OPS_PORT=7681 \
ZHICE_OPS_TERMINAL_PORT=7682 \
ZHICE_OPS_DASHBOARD_PORT=7683 \
ZHICE_OPS_BASIC_AUTH="$ops_basic_auth" \
ZHICE_PUBLIC_URL="$PUBLIC_URL" \
  caddy validate --config /etc/zhice-ops/Caddyfile --adapter caddyfile >/dev/null

systemctl daemon-reload
systemctl enable zhice-ops-dashboard.service >/dev/null
systemctl enable zhice-ops-terminal.service >/dev/null
systemctl enable zhice-ops.service >/dev/null
systemctl restart zhice-ops-dashboard.service
systemctl restart zhice-ops-terminal.service
systemctl restart zhice-ops.service

echo "ZhiCe restricted Ops installed on loopback."
echo "Route the private Ops hostname through the existing Cloudflare Tunnel to 127.0.0.1:7681."
echo "The root-only Ops credential was preserved or generated without printing it."
