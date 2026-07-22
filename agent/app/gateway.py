"""FastAPI gateway for the local ZhiCe-Agent Web surface."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agent.app.api.routes import ApiError, router
from agent.app.api.ws import router as ws_router
from agent.app.auth import AuthHttpError
from agent.app.logging import GatewayLogOptions, configure_gateway_logging
from agent.app.runtime import WebRuntime, build_web_runtime
from agent.config import AppConfig
from agent.console import console
from agent.protocols.auth import AuditEvent
from agent.protocols.capability import CapabilityStatus
from agent.protocols.errors import ErrorCode


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
        print(console.warning("gateway auth is enabled, but this remains a local development service."))
    print(f"workspace: {console.path(config.workspace)}")
    print(f"static: {console.path(static_dir)}")
    print("routes: /, /_setup, /health, /api/*, /ws")
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        shutdown = getattr(runtime, "shutdown", None)
        if callable(shutdown):
            shutdown()

    app = FastAPI(
        title="ZhiCe-Agent Gateway",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.runtime = runtime
    app.state.auth_service = getattr(runtime, "auth", None)

    @app.middleware("http")
    async def attach_request_id(request, call_next):
        request.state.request_id = "req-" + uuid.uuid4().hex
        auth_service = getattr(request.app.state, "auth_service", None)
        protected_api = request.url.path.startswith("/api/") and request.url.path not in {
            "/api/auth/bootstrap",
            "/api/auth/login",
            "/api/auth/register",
            "/api/health",
        }
        if protected_api and auth_service is not None:
            try:
                request.state.actor = auth_service.resolve_request_actor(request, channel="rest")
            except AuthHttpError as exc:
                if auth_service.audit_sink is not None:
                    auth_service.audit_sink.record(
                        AuditEvent(
                            action="auth.request_denied",
                            resource_type="http_request",
                            request_id=request.state.request_id,
                            channel="rest",
                            route=request.url.path,
                            status_code=exc.status_code,
                            decision="deny",
                            reason_code=exc.code,
                        )
                    )
                return JSONResponse(
                    status_code=exc.status_code,
                    content=_error_content(
                        status=exc.status_code,
                        code=exc.code,
                        message=exc.message,
                        request_id=request.state.request_id,
                        details=exc.details,
                    ),
                    headers={"X-Request-ID": request.state.request_id},
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

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

    @app.get("/admin", include_in_schema=False)
    def administration():
        index_path = resolved_static_dir / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return Response(status_code=404)

    @app.get("/_setup", include_in_schema=False)
    def setup_owner():
        auth_service = getattr(app.state, "auth_service", None)
        if (
            auth_service is None
            or not auth_service.setup_token
            or auth_service.store.has_owner()
        ):
            return Response(status_code=404)
        index_path = resolved_static_dir / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return Response(status_code=404)

    @app.get("/health")
    def health():
        return gateway_status(config, runtime=runtime)

    @app.get("/api/health")
    def api_health():
        return gateway_status(config, runtime=runtime)

    @app.get("/favicon.ico")
    def favicon():
        favicon_path = resolved_static_dir / "zhice-logo-a.png"
        if favicon_path.is_file():
            return FileResponse(favicon_path, media_type="image/png")
        return Response(status_code=404)

    @app.get("/.well-known/appspecific/com.chrome.devtools.json")
    def chrome_devtools_workspace_probe():
        return Response(status_code=204)

    return app


def gateway_status(
    config: AppConfig,
    *,
    runtime: WebRuntime | Any | None = None,
) -> dict[str, Any]:
    """Return the status payload exposed by the health endpoint."""

    return {
        "status": "ok",
        "name": "ZhiCe-Agent",
        "current_model": _current_model(runtime),
        "auth_required": "true" if getattr(runtime, "auth", None) is not None else "false",
        "auth_initialized": (
            "true"
            if getattr(getattr(runtime, "auth", None), "store", None)
            and runtime.auth.store.has_users()
            else "false"
        ),
        "owner_initialized": (
            "true"
            if getattr(getattr(runtime, "auth", None), "store", None)
            and runtime.auth.store.has_owner()
            else "false"
        ),
        "capabilities": {
            name: _public_capability_status(name, status)
            for name, status in _capability_statuses(runtime).items()
        },
    }


def format_gateway_check(config: AppConfig, *, host: str, port: int) -> str:
    """Format a non-blocking gateway readiness check for CLI tests and setup."""

    lines = [
        console.success("ZhiCe-Agent gateway check ok"),
        f"url: {console.command(f'http://{host}:{port}')}",
        f"workspace: {console.path(config.workspace)}",
        f"config: {console.path(config.config_dir)}",
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
    def handle_api_error(request, exc: ApiError):
        request_id = str(getattr(request.state, "request_id", ""))
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(
                status=exc.status_code,
                code=exc.code,
                message=exc.message,
                request_id=request_id,
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request, exc: RequestValidationError):
        request_id = str(getattr(request.state, "request_id", ""))
        return JSONResponse(
            status_code=400,
            content=_error_content(
                status=400,
                code=ErrorCode.REQUEST_VALIDATION_FAILED,
                message="invalid request",
                request_id=request_id,
                details=_validation_details(exc),
            ),
        )


def _error_content(
    *,
    status: int,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable HTTP error envelope."""

    return {
        "error": {
            "status": int(status),
            "code": str(code),
            "message": str(message),
            "request_id": str(request_id),
            "details": dict(details or {}),
        }
    }


def _validation_details(exc: RequestValidationError) -> dict[str, Any]:
    """Return field-level validation facts without echoing request values."""

    issues = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc") or ())
        issues.append({"field": location, "reason": str(item.get("type") or "invalid")})
    return {"issues": issues}


def _current_model(runtime: WebRuntime | Any | None) -> str:
    """Return the model label for health output without failing health checks."""

    if runtime is None:
        return "unavailable"
    try:
        return str(runtime.current_model_label())
    except Exception:  # noqa: BLE001 - health should stay stable.
        return "unavailable"


def _capability_statuses(runtime: WebRuntime | Any | None) -> dict[str, CapabilityStatus]:
    """Return generic safe optional-capability statuses without failing health."""

    if runtime is not None:
        provider = getattr(runtime, "capability_statuses", None)
        if callable(provider):
            try:
                statuses = provider()
                if isinstance(statuses, dict) and all(
                    isinstance(name, str) and isinstance(status, CapabilityStatus)
                    for name, status in statuses.items()
                ):
                    return statuses
            except Exception:  # noqa: BLE001 - health must remain available.
                pass
        discovered = {
            name.removesuffix("_status"): status
            for name, status in vars(runtime).items()
            if name.endswith("_status") and isinstance(status, CapabilityStatus)
        }
        if discovered:
            return discovered
    return {
        "subagent": CapabilityStatus(
            name="subagent",
            state="unavailable",
            code="SUBAGENT_STATUS_UNAVAILABLE",
            message="Subagent capability status is unavailable.",
            hint="Start the Gateway runtime to evaluate optional capabilities.",
        )
    }


def _public_capability_status(name: str, status: CapabilityStatus) -> dict[str, Any]:
    """Return public health state without internal configuration or repair details."""

    messages = {
        "available": f"{name} is available.",
        "disabled": f"{name} is not enabled.",
        "degraded": f"{name} is temporarily limited.",
        "unavailable": f"{name} is temporarily unavailable.",
    }
    return {
        "name": name,
        "state": status.state,
        "code": f"{name.upper()}_{status.state.upper()}",
        "message": messages[status.state],
        "hint": (
            "Contact an administrator."
            if status.state in {"degraded", "unavailable"}
            else ""
        ),
        "details": {},
    }


def _default_static_dir() -> Path:
    """Return the repository static web directory."""

    return Path(__file__).resolve().parents[2] / "web" / "static"
