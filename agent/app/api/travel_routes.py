"""Authenticated REST projection for actor-owned travel plans."""

from __future__ import annotations

from fastapi import APIRouter, Request

from agent.app.api.routes import ApiError, _actor, _runtime
from agent.app.api.schemas import (
    TravelCandidateReviewResponse,
    TravelCandidateSelectionRequest,
    TravelConversationRequest,
    TravelConversationResponse,
    TravelDraftResponse,
    TravelGenerationStatusResponse,
    TravelPlanMutationResponse,
    TravelPlanningConfirmationRequest,
    TravelPlanningConfirmationResponse,
    TravelPlanResponse,
    TravelPlansResponse,
    TravelRequirementExtractionRequest,
    TravelRequirementExtractionResponse,
    TravelWorkItemMutationResponse,
    TravelWorkItemsResponse,
)
from agent.applications.travel.service import TravelApplicationError

router = APIRouter(prefix="/api/travel")


@router.post(
    "/sessions/{session_id}/confirm-planning",
    response_model=TravelPlanningConfirmationResponse,
)
def confirm_travel_planning(
    session_id: str,
    request_body: TravelPlanningConfirmationRequest,
    request: Request,
) -> TravelPlanningConfirmationResponse:
    """Validate reviewed intake state and open the formal planning phase."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    confirmer = getattr(runtime, "confirm_travel_planning", None)
    if not callable(confirmer):
        raise ApiError(
            "TRAVEL_PLANNING_CONFIRMATION_UNAVAILABLE",
            "旅行规划暂时无法开始。",
            status_code=503,
        )
    try:
        payload = confirmer(actor, session_id, request_body.draft)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelPlanningConfirmationResponse(**payload)


@router.get(
    "/sessions/{session_id}/candidate-review",
    response_model=TravelCandidateReviewResponse,
)
def read_candidate_review(session_id: str, request: Request) -> TravelCandidateReviewResponse:
    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    reader = getattr(runtime, "travel_candidate_review", None)
    if not callable(reader):
        raise ApiError("TRAVEL_CANDIDATE_REVIEW_UNAVAILABLE", "候选行程暂时无法读取。", status_code=503)
    try:
        payload = reader(actor, session_id)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelCandidateReviewResponse(**payload)


@router.post(
    "/sessions/{session_id}/candidate-selection",
    response_model=TravelCandidateReviewResponse,
)
def select_candidate(
    session_id: str,
    request_body: TravelCandidateSelectionRequest,
    request: Request,
) -> TravelCandidateReviewResponse:
    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    selector = getattr(runtime, "select_travel_candidate", None)
    if not callable(selector):
        raise ApiError("TRAVEL_CANDIDATE_REVIEW_UNAVAILABLE", "候选行程暂时无法选择。", status_code=503)
    try:
        payload = selector(actor, session_id, request_body.candidate_id)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelCandidateReviewResponse(**payload)


@router.post(
    "/sessions/{session_id}/conversation",
    response_model=TravelConversationResponse,
)
def persist_travel_conversation(
    session_id: str,
    request_body: TravelConversationRequest,
    request: Request,
) -> TravelConversationResponse:
    """Persist reviewed requirement chat into its actor-owned travel Session."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    writer = getattr(runtime, "persist_travel_conversation", None)
    if not callable(writer):
        raise ApiError(
            "TRAVEL_CONVERSATION_UNAVAILABLE",
            "旅行需求对话暂时无法保存。",
            status_code=503,
        )
    try:
        messages = [message.model_dump() for message in request_body.messages]
        payload = (
            writer(actor, session_id, messages, draft=request_body.draft)
            if request_body.draft
            else writer(actor, session_id, messages)
        )
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelConversationResponse(**payload)


@router.get("/sessions/{session_id}/draft", response_model=TravelDraftResponse)
def read_travel_draft(session_id: str, request: Request) -> TravelDraftResponse:
    """Read one actor-owned travel requirement draft."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    reader = getattr(runtime, "travel_draft", None)
    if not callable(reader):
        raise ApiError("TRAVEL_DRAFT_UNAVAILABLE", "旅行草稿暂时无法读取。", status_code=503)
    try:
        payload = reader(actor, session_id)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelDraftResponse(**payload)


@router.get("/work-items", response_model=TravelWorkItemsResponse)
def list_travel_work_items(request: Request, limit: int = 50) -> TravelWorkItemsResponse:
    """List all current-actor travel lifecycle states in one projection."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    reader = getattr(runtime, "list_travel_work_items", None)
    if not callable(reader):
        raise ApiError("TRAVEL_WORK_ITEMS_UNAVAILABLE", "旅行任务暂时无法读取。", status_code=503)
    try:
        return TravelWorkItemsResponse(items=reader(actor, limit=limit))
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc


@router.delete(
    "/sessions/{session_id}", response_model=TravelWorkItemMutationResponse
)
def delete_travel_work_item(
    session_id: str, request: Request
) -> TravelWorkItemMutationResponse:
    """Delete one unfinished actor-owned travel Session."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    deleter = getattr(runtime, "delete_travel_work_item", None)
    if not callable(deleter):
        raise ApiError("TRAVEL_WORK_ITEM_DELETE_UNAVAILABLE", "旅行任务暂时无法删除。", status_code=503)
    try:
        deleter(actor, session_id)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelWorkItemMutationResponse(session_id=session_id, status="deleted")


@router.get("/generation", response_model=TravelGenerationStatusResponse)
def read_travel_generation(
    request: Request,
    session_id: str = "",
) -> TravelGenerationStatusResponse:
    """Project one actor-owned travel Turn into a refresh-safe UI state."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    reader = getattr(runtime, "travel_generation_status", None)
    if not callable(reader):
        raise ApiError(
            "TRAVEL_GENERATION_STATUS_UNAVAILABLE",
            "旅行规划状态暂不可用。",
            status_code=503,
        )
    try:
        payload = reader(actor, session_id=str(session_id or "").strip())
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelGenerationStatusResponse(**payload)


@router.post("/requirements/extract", response_model=TravelRequirementExtractionResponse)
def extract_travel_requirements(
    request_body: TravelRequirementExtractionRequest,
    request: Request,
) -> TravelRequirementExtractionResponse:
    """Use the configured LLMProvider to create a review-only requirement draft."""

    runtime = _runtime(request)
    _actor(request, channel="rest")
    extractor = getattr(runtime, "travel_requirement_extractor", None)
    if extractor is None:
        raise ApiError(
            "TRAVEL_REQUIREMENT_EXTRACTION_UNAVAILABLE",
            "旅行需求提取暂不可用。",
            status_code=503,
        )
    try:
        draft = extractor.extract(request_body.text)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    payload = draft.to_dict()
    return TravelRequirementExtractionResponse(
        draft=payload,
        missing_fields=(
            _missing_requirement_fields(payload)
            if payload.get("intent") == "travel_requirement"
            else []
        ),
    )


@router.get("/plans", response_model=TravelPlansResponse)
def list_travel_plans(request: Request, limit: int = 50) -> TravelPlansResponse:
    """List plan metadata for the authenticated actor only."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    service = _travel_service(runtime)
    try:
        plans = service.list_plans(actor, limit=limit)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelPlansResponse(plans=[item.to_dict() for item in plans])


@router.get("/plans/{plan_id}", response_model=TravelPlanResponse)
def read_travel_plan(plan_id: str, request: Request) -> TravelPlanResponse:
    """Read one plan only when its owner matches the current actor."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        plan = _travel_service(runtime).get_plan(actor, plan_id)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelPlanResponse(plan=plan.to_dict())


@router.delete("/plans/{plan_id}", response_model=TravelPlanMutationResponse)
def delete_travel_plan(plan_id: str, request: Request) -> TravelPlanMutationResponse:
    """Delete one plan only when its owner matches the current actor."""

    runtime = _runtime(request)
    actor = _actor(request, channel="rest")
    try:
        deleter = getattr(runtime, "delete_travel_plan", None)
        if callable(deleter):
            deleter(actor, plan_id)
        else:
            _travel_service(runtime).delete_plan(actor, plan_id)
    except TravelApplicationError as exc:
        raise _api_error(exc) from exc
    return TravelPlanMutationResponse(plan_id=plan_id, status="deleted")


def _travel_service(runtime):
    service = getattr(runtime, "travel_service", None)
    if service is None:
        raise ApiError(
            "TRAVEL_DISABLED",
            "Travel planning is not enabled for this workspace.",
            status_code=503,
        )
    return service


def _api_error(exc: TravelApplicationError) -> ApiError:
    details = {"field": exc.field} if exc.field else {}
    return ApiError(exc.code, exc.message, status_code=exc.status_code, details=details)


def _missing_requirement_fields(draft: dict) -> list[str]:
    checks = (
        ("origin", "出发地"),
        ("destinations", "目的地"),
        ("start_date", "开始日期"),
        ("end_date", "结束日期"),
        ("traveller_count", "人数"),
    )
    return [label for key, label in checks if not draft.get(key)]
