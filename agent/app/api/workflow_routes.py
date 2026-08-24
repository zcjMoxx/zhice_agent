"""Authenticated owner-scoped workflow REST API."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request

from agent.app.api.routes import ApiError, _actor, _runtime
from agent.connections.protocols import ConnectionError
from agent.workflows.catalog import WorkflowValidationError
from agent.workflows.node_red import NodeRedFlowError, compile_flow, parse_flow
from agent.workflows.schemas import WorkflowDefinitionV1

router = APIRouter(prefix="/api")

_KNOWN_ERROR_PREFIXES = ("WORKFLOW_", "CONNECTION_", "OFFICIAL_", "NOTIFICATION_")


def _map_workflow_error(exc: Exception, *, fallback: str = "WORKFLOW_NODE_FAILED") -> ApiError:
    """Convert expected workflow failures before they become opaque HTTP 500 responses."""

    if isinstance(exc, WorkflowValidationError):
        return ApiError(exc.code, str(exc), status_code=422)
    if isinstance(exc, ConnectionError):
        return ApiError(exc.code, str(exc), status_code=400)
    raw = str(exc).strip("'\"")
    code = raw if raw.startswith(_KNOWN_ERROR_PREFIXES) else fallback
    if isinstance(exc, PermissionError):
        status = 403
    elif isinstance(exc, KeyError):
        status = 404
    elif isinstance(exc, (ValueError, TypeError)):
        status = 422
    else:
        status = 502
    return ApiError(code, raw or code, status_code=status)


def _service(request: Request):
    service = getattr(_runtime(request), "workflow_runtime", None)
    if service is None:
        raise ApiError("WORKFLOW_DISABLED", "Workflow runtime is disabled.", status_code=503)
    return service


def _owner(request: Request) -> str:
    actor = _actor(request, channel="rest")
    if not actor.user_id:
        raise ApiError("WORKFLOW_ACCESS_DENIED", "A database user is required.", status_code=403)
    return actor.user_id


def _workflow_payload(service: Any, item: WorkflowDefinitionV1, owner: str) -> dict[str, Any]:
    return {
        **item.to_dict(),
        **service.store.workflow_state(item.workflow_id, owner_user_id=owner),
    }


@router.get("/workflows")
def list_workflows(request: Request) -> dict[str, Any]:
    service = _service(request)
    owner = _owner(request)
    return {
        "items": [
            _workflow_payload(service, item, owner)
            for item in service.store.list_definitions(owner)
        ]
    }


@router.get("/workflow-tools")
def list_workflow_tools(request: Request) -> dict[str, Any]:
    actor = _actor(request, channel="rest")
    _owner(request)
    return {"items": _service(request).tool_catalog(actor)}


@router.get("/workflow-capabilities")
def get_workflow_capabilities(request: Request) -> dict[str, Any]:
    actor = _actor(request, channel="rest")
    _owner(request)
    return _service(request).capabilities(actor)


@router.post("/workflow-tools/test")
def test_workflow_tool(body: dict[str, Any], request: Request) -> dict[str, Any]:
    actor = _actor(request, channel="workflow")
    _owner(request)
    name = str(body.get("name") or "")
    arguments = body.get("arguments")
    if not name or not isinstance(arguments, dict):
        raise ApiError("WORKFLOW_NODE_CONFIG_INVALID", "Tool name and arguments are required.", status_code=422)
    try:
        output = _service(request).test_query_tool(actor, name, arguments)
    except (PermissionError, RuntimeError, ValueError, TypeError, KeyError, ConnectionError) as exc:
        raise _map_workflow_error(exc) from exc
    return {"status": "succeeded", "output": output}


@router.post("/workflows")
def create_workflow(body: dict[str, Any], request: Request) -> dict[str, Any]:
    owner = _owner(request)
    service = _service(request)
    payload = dict(body)
    payload.setdefault("workflow_id", str(uuid4()))
    payload["owner_user_id"] = owner
    try:
        definition = WorkflowDefinitionV1.from_dict(payload)
        item = service.save_draft(_actor(request, channel="rest"), definition)
        return _workflow_payload(service, item, owner)
    except (PermissionError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise _map_workflow_error(exc, fallback="WORKFLOW_SCHEMA_INVALID") from exc


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    service = _service(request)
    owner = _owner(request)
    item = service.store.get_draft(workflow_id, owner_user_id=owner)
    if item is None:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404)
    return _workflow_payload(service, item, owner)


@router.put("/workflows/{workflow_id}/draft")
def update_draft(workflow_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    service = _service(request)
    owner = _owner(request)
    payload = dict(body)
    payload["workflow_id"] = workflow_id
    payload["owner_user_id"] = owner
    expected = payload.pop("expected_version", None)
    try:
        item = WorkflowDefinitionV1.from_dict(payload)
        saved = service.save_draft(
            _actor(request, channel="rest"), item, expected_version=expected
        )
        return _workflow_payload(service, saved, owner)
    except (PermissionError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise _map_workflow_error(exc, fallback="WORKFLOW_SCHEMA_INVALID") from exc


@router.post("/workflows/{workflow_id}/publish")
def publish_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    service = _service(request)
    owner = _owner(request)
    item = service.store.get_draft(workflow_id, owner_user_id=owner)
    if item is None:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404)
    try:
        published = service.publish(_actor(request, channel="rest"), item)
        return _workflow_payload(service, published, owner)
    except (PermissionError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise _map_workflow_error(exc, fallback="WORKFLOW_PUBLISH_FAILED") from exc


@router.post("/workflows/{workflow_id}/pause")
def pause_workflow(workflow_id: str, request: Request) -> dict[str, str]:
    try:
        _service(request).pause(_actor(request, channel="rest"), workflow_id)
    except KeyError as exc:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404) from exc
    return {"status": "paused"}


@router.post("/workflows/{workflow_id}/resume")
def resume_workflow(workflow_id: str, request: Request) -> dict[str, str]:
    try:
        _service(request).resume(_actor(request, channel="rest"), workflow_id)
    except KeyError as exc:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404) from exc
    return {"status": "active"}


@router.post("/workflows/{workflow_id}/run")
def run_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).run(_actor(request, channel="workflow"), workflow_id)
    except KeyError as exc:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404) from exc
    except (PermissionError, RuntimeError, ValueError, TypeError, ConnectionError) as exc:
        raise _map_workflow_error(exc) from exc


@router.post("/workflows/{workflow_id}/run-draft")
def run_workflow_draft(workflow_id: str, request: Request) -> dict[str, Any]:
    """Run the latest saved draft once without publishing it."""

    try:
        return _service(request).run_draft(
            _actor(request, channel="workflow"), workflow_id
        )
    except KeyError as exc:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404) from exc
    except (PermissionError, RuntimeError, ValueError, TypeError, ConnectionError) as exc:
        raise _map_workflow_error(exc) from exc


@router.get("/workflows/{workflow_id}/runs")
def list_workflow_runs(workflow_id: str, request: Request, limit: int = 100) -> dict[str, Any]:
    return {"items": _service(request).store.list_runs(workflow_id, _owner(request), limit)}


@router.get("/workflow-runs/{run_id}")
def get_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    item = _service(request).store.get_run(run_id, _owner(request))
    if item is None:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow run not found.", status_code=404)
    return item


@router.post("/workflow-runs/{run_id}/cancel")
def cancel_workflow_run(run_id: str, request: Request) -> dict[str, str]:
    service = _service(request)
    if service.store.get_run(run_id, _owner(request)) is None:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow run not found.", status_code=404)
    service.executor.cancel(run_id)
    return {"status": "cancelling"}


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, request: Request) -> dict[str, str]:
    try:
        _service(request).store.delete(workflow_id, _owner(request))
    except KeyError as exc:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404) from exc
    return {"status": "deleted"}


@router.get("/workflows/{workflow_id}/node-red")
def export_node_red(workflow_id: str, request: Request) -> dict[str, Any]:
    """Export only the safe, reviewed ZhiCe subset as a Node-RED flow."""
    item = _service(request).store.get_draft(workflow_id, owner_user_id=_owner(request))
    if item is None:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404)
    return {"format": "node-red", "schema_version": 1, "nodes": compile_flow(item)}


@router.post("/workflows/{workflow_id}/node-red")
def import_node_red(workflow_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Import a restricted flow while keeping ownership and draft semantics."""
    owner = _owner(request)
    current = _service(request).store.get_draft(workflow_id, owner_user_id=owner)
    if current is None:
        raise ApiError("WORKFLOW_NOT_FOUND", "Workflow not found.", status_code=404)
    try:
        definition = parse_flow(body.get("nodes", body.get("flow", [])), owner_user_id=owner,
                                workflow_id=workflow_id, name=str(body.get("name") or current.name))
    except NodeRedFlowError as exc:
        raise ApiError("WORKFLOW_SCHEMA_INVALID", str(exc), status_code=422) from exc
    service = _service(request)
    saved = service.save_draft(_actor(request, channel="rest"), definition)
    return _workflow_payload(service, saved, owner)
