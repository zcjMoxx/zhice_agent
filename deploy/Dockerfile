# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS web-build
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

FROM node:22-bookworm-slim AS runtime
ARG ZHICE_VERSION=dev
ARG ZHICE_REVISION=unknown
ARG ZHICE_BUILD_DATE=unknown
LABEL org.opencontainers.image.title="ZhiCe-Agent" \
      org.opencontainers.image.version="${ZHICE_VERSION}" \
      org.opencontainers.image.revision="${ZHICE_REVISION}" \
      org.opencontainers.image.created="${ZHICE_BUILD_DATE}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH \
    HOME=/home/zhice \
    ZHICE_AGENT_SKILL_REPO=/app/skill_repo

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && useradd --create-home --home-dir /home/zhice --shell /usr/sbin/nologin zhice

WORKDIR /app
COPY pyproject.toml README.md ./
COPY agent/ ./agent/
COPY config/ ./config/
COPY prompts/ ./prompts/
COPY skill_repo/ ./skill_repo/
COPY integrations/ ./integrations/
COPY --from=web-build /src/agent/web/static/ ./agent/web/static/
COPY --from=weixin-build /src/integrations/weixin_sidecar/dist/ ./integrations/weixin_sidecar/dist/
COPY --from=weixin-build /src/integrations/weixin_sidecar/node_modules/ ./integrations/weixin_sidecar/node_modules/

RUN pip install --no-cache-dir ".[qq]"

# These are the only local private build inputs. Runtime state is never copied.
COPY --chown=zhice:zhice deploy/.env /home/zhice/.zhice/config/.env
COPY --chown=zhice:zhice deploy/config.yml /home/zhice/.zhice/config/config.yml
COPY --chown=zhice:zhice deploy/models.json /home/zhice/.zhice/config/models.json
COPY --chown=zhice:zhice prompts/ /home/zhice/.zhice/prompts/

RUN mkdir -p /home/zhice/.zhice/contexts /home/zhice/.zhice/state \
        /home/zhice/.zhice/logs /home/zhice/.zhice/extends \
    && chown -R zhice:zhice /home/zhice/.zhice /app/integrations/weixin_sidecar

USER zhice
EXPOSE 10086
VOLUME ["/home/zhice/.zhice/contexts", "/home/zhice/.zhice/state", "/home/zhice/.zhice/logs", "/home/zhice/.zhice/extends"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10086/health', timeout=3).read()" || exit 1

ENTRYPOINT ["zcagent", "--env-file", "/home/zhice/.zhice/config/.env"]
CMD ["gateway", "--host", "0.0.0.0", "--port", "10086"]
