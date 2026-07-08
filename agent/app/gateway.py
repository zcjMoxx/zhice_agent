"""FastAPI gateway for the local ZhiCe-Agent Web surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agent.app.api.routes import ApiError, router
from agent.app.api.ws import router as ws_router
from agent.app.logging import GatewayLogOptions, configure_gateway_logging
from agent.app.runtime import WebRuntime, build_web_runtime
from agent.config import AppConfig
from agent.console import console


def run_gateway(
    config: AppConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 10086,
    log_options: GatewayLogOptions | None = None,
) -> None:
    """Start the local FastAPI gateway and serve until interrupted."""

    resolved_log_options = log_options or GatewayLogOptions()
    logging_result = configure_gateway_logging(resolved_log_options, logs_dir=config.logs_dir)
    runtime = build_web_runtime(config)
    static_dir = _default_static_dir()
    app = create_app(config=config, runtime=runtime, static_dir=static_dir)
    print(
        f"{console.bold('ZhiCe-Agent gateway')} listening on "
        f"{console.command(f'http://{host}:{port}')}"
    )
    if host in {"0.0.0.0", "::"}:
        print(console.warning("gateway is a local development service and has no built-in auth."))
    print(f"workspace: {console.path(config.workspace)}")
    print(f"static: {console.path(static_dir)}")
    print("routes: /, /health, /api/*, /ws")
    print(
        "agent-log: "
        f"{'on' if resolved_log_options.agent_log else 'off'} "
        f"level={resolved_log_options.agent_log_level}"
    )
    print(
        "http-access-log: "
        f"{'on' if resolved_log_options.http_access_log else 'off'}, "
        "http-server-log: "
        f"{'on' if resolved_log_options.http_server_log else 'off'} "
        f"level={resolved_log_options.http_server_log_level}"
    )
    trace_path = logging_result.trace_path or config.logs_dir / "YYYY-MM-DD" / "trace.log"
    print(f"trace-log: {'on' if resolved_log_options.trace_log else 'off'} path={console.path(trace_path)}")
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=_uvicorn_log_level(resolved_log_options),
        access_log=resolved_log_options.http_access_log,
    )


def create_app(
    *,
    config: AppConfig,
    runtime: WebRuntime | Any | None = None,
    static_dir: Path | str | None = None,
) -> FastAPI:
    """Create the FastAPI app used by tests and the gateway command."""

    app = FastAPI(title="ZhiCe-Agent Gateway", docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.runtime = runtime
    app.include_router(router)
    app.include_router(ws_router)
    _register_error_handlers(app)

    resolved_static_dir = Path(static_dir).expanduser().resolve() if static_dir else _default_static_dir()
    if resolved_static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=resolved_static_dir), name="static")

    @app.get("/")
    def index():
        index_path = resolved_static_dir / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return JSONResponse(gateway_status(config, runtime=runtime))

    @app.get("/health")
    def health():
        return gateway_status(config, runtime=runtime)

    @app.get("/favicon.ico")
    def favicon():
        return Response(content=_FAVICON_SVG, media_type="image/svg+xml")

    @app.get("/.well-known/appspecific/com.chrome.devtools.json")
    def chrome_devtools_workspace_probe():
        return Response(status_code=204)

    return app


def gateway_status(
    config: AppConfig,
    *,
    runtime: WebRuntime | Any | None = None,
) -> dict[str, str]:
    """Return the status payload exposed by the health endpoint."""

    return {
        "status": "ok",
        "name": "ZhiCe-Agent",
        "workspace": str(config.workspace),
        "config_dir": str(config.config_dir),
        "sessions_dir": str(config.sessions_dir),
        "current_model": _current_model(runtime),
    }


def format_gateway_check(config: AppConfig, *, host: str, port: int) -> str:
    """Format a non-blocking gateway readiness check for CLI tests and setup."""

    payload = gateway_status(config)
    lines = [
        console.success("ZhiCe-Agent gateway check ok"),
        f"url: {console.command(f'http://{host}:{port}')}",
        f"workspace: {console.path(payload['workspace'])}",
        f"config: {console.path(Path(payload['config_dir']))}",
    ]
    return "\n".join(lines)


def _uvicorn_log_level(options: GatewayLogOptions) -> str:
    """Return the uvicorn log level for the selected HTTP server log mode."""

    if not options.http_server_log:
        return "critical"
    return options.http_server_log_level


def _register_error_handlers(app: FastAPI) -> None:
    """Register API error handlers with stable JSON shapes."""

    @app.exception_handler(ApiError)
    def handle_api_error(_request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(_request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_REQUEST", "message": "invalid request"}},
        )


def _current_model(runtime: WebRuntime | Any | None) -> str:
    """Return the model label for health output without failing health checks."""

    if runtime is None:
        return "unavailable"
    try:
        return str(runtime.current_model_label())
    except Exception:  # noqa: BLE001 - health should stay stable.
        return "unavailable"


def _default_static_dir() -> Path:
    """Return the repository static web directory."""

    return Path(__file__).resolve().parents[2] / "web" / "static"


_FAVICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#111827"/>
  <path d="M17 20h30L25 44h22" fill="none" stroke="#f9fafb" stroke-width="7"
        stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="46" cy="18" r="6" fill="#38bdf8"/>
  <text x="32" y="53" text-anchor="middle" font-family="Arial, sans-serif"
        font-size="10" font-weight="700" fill="#f9fafb">ZC</text>
</svg>
"""
