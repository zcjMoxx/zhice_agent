"""Authenticated REST API for user-owned email connections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from agent.app.api.routes import ApiError, _actor, _runtime
from agent.connections.protocols import ConnectionError

router = APIRouter(prefix="/api/connections")


class SMTPConnectionRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int
    security: str
    username: str = Field(min_length=1, max_length=320)
    app_password: str = Field(min_length=1, max_length=1024)


class TestEmailRequest(BaseModel):
    recipient: str = Field(min_length=3, max_length=320)


class NotificationEmailRequest(BaseModel):
    address: str = Field(min_length=3, max_length=320)


class NotificationEmailVerificationRequest(NotificationEmailRequest):
    code: str = Field(min_length=6, max_length=12)


def _connections(request: Request):
    service = getattr(_runtime(request), "connection_runtime", None)
    if service is None:
        raise ApiError("CONNECTION_PROVIDER_UNSUPPORTED", "external connections are unavailable", status_code=503)
    return service


def _map_error(exc: ConnectionError) -> ApiError:
    status = 404 if exc.code == "CONNECTION_NOT_FOUND" else 403 if exc.code == "CONNECTION_ACCESS_DENIED" else 400
    if exc.code == "NOTIFICATION_EMAIL_VERIFICATION_RATE_LIMITED":
        status = 429
    if exc.code in {
        "CONNECTION_PROVIDER_UNSUPPORTED",
        "CONNECTION_CREDENTIAL_KEY_MISSING",
        "NOTIFICATION_EMAIL_UNAVAILABLE",
        "OFFICIAL_EMAIL_NOT_CONFIGURED",
    }:
        status = 503
    return ApiError(exc.code, str(exc), status_code=status, details=exc.details)


@router.get("")
def list_connections(request: Request) -> dict[str, Any]:
    actor = _actor(request, "workflow.use", channel="rest")
    return {"connections": _connections(request).list(actor)}


@router.post("/email/smtp")
def create_smtp_connection(body: SMTPConnectionRequest, request: Request) -> dict[str, Any]:
    actor = _actor(request, "workflow.email.send", channel="rest")
    try:
        connection = _connections(request).create_personal_smtp(actor, **body.model_dump())
    except ConnectionError as exc:
        raise _map_error(exc) from exc
    return {"connection": connection}


@router.get("/email/notification")
def get_notification_email(request: Request) -> dict[str, Any]:
    actor = _actor(request, "workflow.notify.self", channel="rest")
    try:
        return {"email": _connections(request).notification_email(actor)}
    except ConnectionError as exc:
        raise _map_error(exc) from exc


@router.post("/email/notification/request-verification")
def request_notification_email_verification(
    body: NotificationEmailRequest, request: Request
) -> dict[str, Any]:
    actor = _actor(request, "workflow.notify.self", channel="rest")
    try:
        return _connections(request).request_notification_email_verification(
            actor, address=body.address
        )
    except ConnectionError as exc:
        raise _map_error(exc) from exc


@router.post("/email/notification/verify")
def verify_notification_email(
    body: NotificationEmailVerificationRequest, request: Request
) -> dict[str, Any]:
    actor = _actor(request, "workflow.notify.self", channel="rest")
    try:
        return {
            "email": _connections(request).verify_notification_email(
                actor, address=body.address, code=body.code
            )
        }
    except ConnectionError as exc:
        raise _map_error(exc) from exc


@router.post("/email/notification/test")
def test_notification_email(request: Request) -> dict[str, Any]:
    actor = _actor(request, "workflow.notify.self", channel="rest")
    try:
        return _connections(request).send_notification_test(actor)
    except ConnectionError as exc:
        raise _map_error(exc) from exc


@router.delete("/{connection_id}")
def delete_connection(connection_id: str, request: Request) -> dict[str, bool]:
    actor = _actor(request, "workflow.email.send", channel="rest")
    try:
        _connections(request).delete(actor, connection_id)
    except ConnectionError as exc:
        raise _map_error(exc) from exc
    return {"deleted": True}


@router.post("/{connection_id}/test-email")
def test_email_connection(
    connection_id: str, body: TestEmailRequest, request: Request
) -> dict[str, Any]:
    actor = _actor(request, "workflow.email.send", channel="rest")
    try:
        return _connections(request).send_test_email(
            actor, connection_id, recipient=body.recipient
        )
    except ConnectionError as exc:
        raise _map_error(exc) from exc
