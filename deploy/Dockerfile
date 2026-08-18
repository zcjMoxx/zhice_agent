# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS web-build
ARG VITE_AMAP_JS_API_KEY=""
ARG VITE_AMAP_JS_SECURITY_CODE=""
ENV VITE_AMAP_JS_API_KEY=${VITE_AMAP_JS_API_KEY} \
    VITE_AMAP_JS_SECURITY_CODE=${VITE_AMAP_JS_SECURITY_CODE}
WORKDIR /src/web/frontend
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS weixin-build
WORKDIR /src/integrations/weixin_sidecar
COPY integrations/weixin_sidecar/package.json integrations/weixin_sidecar/package-lock.json ./
RUN npm ci
COPY integrations/weixin_sidecar/scripts/ ./scripts/
COPY integrations/weixin_sidecar/src/ ./src/
COPY integrations/weixin_sidecar/vendor/ ./vendor/
COPY integrations/weixin_sidecar/LICENSES/ ./LICENSES/
RUN npm run build && npm prune --omit=dev

FROM golang:1.24-bookworm AS xhs-build
ARG XHS_MCP_REVISION=c2fc4dde2c45f26f6f9de288b7423a2bdfa7af1c
ARG GO_MODULE_PROXY=https://proxy.golang.org,direct
ARG APT_MIRROR=""
ENV GOPROXY=${GO_MODULE_PROXY}
RUN case "$APT_MIRROR" in \
        "") ;; \
        *[!A-Za-z0-9.:-]*) echo "Invalid APT_MIRROR host" >&2; exit 2 ;; \
        *) sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources ;; \
    esac \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone https://github.com/xpzouying/xiaohongshu-mcp.git . \
    && git checkout --detach "$XHS_MCP_REVISION"
COPY deploy/patches/xiaohongshu-mcp-rednote-v2.4.3.patch /tmp/xhs-rednote.patch
RUN git apply --check /tmp/xhs-rednote.patch \
    && git apply /tmp/xhs-rednote.patch \
    && for attempt in 1 2 3; do \
         go mod download && break; \
         if [ "$attempt" -eq 3 ]; then exit 1; fi; \
         sleep $((attempt * 2)); \
       done \
    && go test ./xiaohongshu \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/xiaohongshu-mcp-rednote . \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/xiaohongshu-login-rednote ./cmd/login

FROM node:22-bookworm-slim AS runtime
ARG ZHICE_VERSION=dev
ARG ZHICE_REVISION=unknown
ARG ZHICE_BUILD_DATE=unknown
ARG APT_MIRROR=""
LABEL org.opencontainers.image.title="ZhiCe-Agent" \
      org.opencontainers.image.version="${ZHICE_VERSION}" \
      org.opencontainers.image.revision="${ZHICE_REVISION}" \
      org.opencontainers.image.created="${ZHICE_BUILD_DATE}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/zhice/playwright \
    HOTEL_BROWSER_CHANNEL="" \
    HOME=/home/zhice

RUN case "$APT_MIRROR" in \
        "") ;; \
        *[!A-Za-z0-9.:-]*) echo "Invalid APT_MIRROR host" >&2; exit 2 ;; \
        *) sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources ;; \
    esac \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 \
        libdrm2 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 \
        libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 \
        libxext6 libxfixes3 libxkbcommon0 libxrandr2 xdg-utils \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && useradd --create-home --home-dir /home/zhice --shell /usr/sbin/nologin zhice

RUN npm install --global --omit=dev \
      @amap/amap-maps-mcp-server@0.0.8 \
      12306-mcp@0.3.1 \
    && npm cache clean --force

WORKDIR /app
COPY pyproject.toml README.md ./
COPY agent/ ./agent/
COPY config/ ./config/
COPY prompts/ ./prompts/
COPY skill_repo/ ./skill_repo/
COPY integrations/ ./integrations/
COPY --from=web-build /src/agent/web/static/ ./agent/web/static/
COPY --from=weixin-build --chown=zhice:zhice /src/integrations/weixin_sidecar/dist/ ./integrations/weixin_sidecar/dist/
COPY --from=weixin-build --chown=zhice:zhice /src/integrations/weixin_sidecar/node_modules/ ./integrations/weixin_sidecar/node_modules/
COPY --from=xhs-build --chown=zhice:zhice /out/xiaohongshu-mcp-rednote /opt/zhice/bin/xiaohongshu-mcp-rednote
COPY --from=xhs-build --chown=zhice:zhice /out/xiaohongshu-login-rednote /opt/zhice/bin/xiaohongshu-login-rednote

RUN pip install --no-cache-dir ".[gateway,qq,hotel-browser]" \
    && python -m playwright install chromium \
    && chown -R zhice:zhice /opt/zhice

# These are the only local private build inputs. Runtime state is never copied.
COPY --chown=zhice:zhice deploy/private/.env /home/zhice/.zhice/config/.env
COPY --chown=zhice:zhice deploy/private/config.yml /home/zhice/.zhice/config/config.yml
COPY --chown=zhice:zhice deploy/private/models.json /home/zhice/.zhice/config/models.json
COPY --chown=zhice:zhice prompts/ /home/zhice/.zhice/prompts/

RUN mkdir -p /home/zhice/.zhice/contexts /home/zhice/.zhice/state /home/zhice/.zhice/travel \
        /home/zhice/.zhice/logs /home/zhice/.zhice/extends \
        /home/zhice/.zhice/integrations/xhs/data \
        /home/zhice/.cache/xiaohongshu-mcp \
        /home/zhice/.zhice/config/channels/weixin/accounts \
    && chown -R zhice:zhice /home/zhice/.zhice /home/zhice/.cache

USER zhice
EXPOSE 10086
VOLUME ["/home/zhice/.zhice/contexts", "/home/zhice/.zhice/state", "/home/zhice/.zhice/travel", "/home/zhice/.zhice/logs", "/home/zhice/.zhice/extends", "/home/zhice/.zhice/config/channels/weixin/accounts", "/home/zhice/.zhice/integrations/xhs/data", "/home/zhice/.cache/xiaohongshu-mcp"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10086/health', timeout=3).read()" || exit 1

ENTRYPOINT ["zcagent", "--env-file", "/home/zhice/.zhice/config/.env"]
CMD ["gateway", "--host", "0.0.0.0", "--port", "10086"]
