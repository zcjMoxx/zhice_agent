"""Application service joining travel validation, actor ownership, and persistence."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from agent.applications.travel.config import TravelConfig
from agent.applications.travel.presentation import travel_plan_title
from agent.applications.travel.schemas import TravelPlanV1, TravelValidationError
from agent.applications.travel.source_ledger import TravelSourceLedger
from agent.applications.travel.store import (
    TravelCandidateReview,
    TravelPlanStore,
    TravelPlanStoreError,
    TravelPlanSummary,
)
from agent.protocols.auth import ActorContext


class TravelApplicationError(RuntimeError):
    """Safe application error mapped consistently to Tool and HTTP boundaries."""

    def __init__(self, code: str, message: str, *, status_code: int = 400, field: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.field = field


class TravelApplicationService:
    """Own the travel application's actor scope without owning AgentLoop."""

    def __init__(
        self,
        config: TravelConfig,
        user_contexts,
        source_ledger: TravelSourceLedger | None = None,
    ):
        self.config = config
        self.user_contexts = user_contexts
        self.source_ledger = source_ledger or TravelSourceLedger()

    def tools_for_actor(self, actor: ActorContext):
        """Return the internal domain Tool only for authenticated DB actors."""

        if not self.config.enabled or not actor.user_id:
            return []
        from agent.applications.travel.tools import (
            FinalizeTravelPlanTool,
            RequestTravelCandidateReviewTool,
            RequestTravelClarificationTool,
        )

        workspace = self.user_contexts.workspace_dir
        return [
            FinalizeTravelPlanTool(workspace, self),
            RequestTravelClarificationTool(workspace),
            RequestTravelCandidateReviewTool(workspace, self),
        ]

    def intake_tools_for_actor(self, actor: ActorContext, sessions, *, confirm_planning=None):
        """Return only the bounded confirmation-before-planning travel Tools."""

        if not self.config.enabled or not actor.user_id:
            return []
        from agent.applications.travel.tools import (
            ConfirmAndStartTravelPlanningTool,
            OfferMainChatHandoffTool,
            UpdateTravelDraftTool,
        )

        workspace = self.user_contexts.workspace_dir
        tools = [
            UpdateTravelDraftTool(workspace, sessions),
            OfferMainChatHandoffTool(workspace, sessions),
        ]
        if callable(confirm_planning):
            tools.append(
                ConfirmAndStartTravelPlanningTool(
                    workspace,
                    sessions,
                    confirm_planning,
                )
            )
        return tools

    def finalize(
        self,
        actor: ActorContext,
        raw_plan: object,
        *,
        source_session_id: str,
        source_turn_id: str,
        selected_candidate_id: str = "",
        require_candidate_review: bool = False,
    ) -> TravelPlanV1:
        """Validate, re-own, and atomically persist one model-produced plan."""

        self._require_enabled()
        owner_user_id = self._owner_user_id(actor)
        self._require_candidate_selection(
            actor,
            source_session_id,
            raw_plan=raw_plan,
            selected_candidate_id=selected_candidate_id,
            required=require_candidate_review,
        )
        try:
            plan = TravelPlanV1.from_dict(
                raw_plan,
                max_evidence_items=self.config.max_evidence_items,
                max_plan_bytes=self.config.max_plan_bytes,
            )
        except TravelValidationError as exc:
            raise TravelApplicationError(
                exc.code, exc.message, status_code=400, field=exc.field
            ) from exc
        plan_id = "travel-plan-" + uuid.uuid4().hex
        owned = plan.with_identity(plan_id=plan_id, owner_user_id=owner_user_id)
        store = self.store_for_actor(actor)
        try:
            saved = store.save(
                owned,
                owner_user_id=owner_user_id,
                source_session_id=source_session_id,
                source_turn_id=source_turn_id,
                title=travel_plan_title(owned.request),
            )
            store.clear_candidate_review(owner_user_id, source_session_id)
            return saved
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def list_plans(self, actor: ActorContext, *, limit: int = 50) -> list[TravelPlanSummary]:
        """List only the current user's plan metadata."""

        self._require_enabled()
        owner_user_id = self._owner_user_id(actor)
        try:
            return self.store_for_actor(actor).list(owner_user_id, limit=limit)
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def get_plan(self, actor: ActorContext, plan_id: str) -> TravelPlanV1:
        """Load only the current user's plan body."""

        self._require_enabled()
        owner_user_id = self._owner_user_id(actor)
        try:
            return self.store_for_actor(actor).get(owner_user_id, _plan_id(plan_id))
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def delete_plan(self, actor: ActorContext, plan_id: str) -> str:
        """Delete only the current user's plan and return its source Session id."""

        self._require_enabled()
        owner_user_id = self._owner_user_id(actor)
        try:
            return self.store_for_actor(actor).delete(owner_user_id, _plan_id(plan_id))
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def store_for_actor(self, actor: ActorContext) -> TravelPlanStore:
        """Derive the Store path from the trusted actor context resolver."""

        owner_user_id = self._owner_user_id(actor)
        context = self.user_contexts.resolve(
            owner_user_id,
            use_workspace_context="owner" in actor.role_keys,
        )
        root = Path(context.root_dir).resolve()
        return TravelPlanStore(root)

    def save_candidate_review(
        self,
        actor: ActorContext,
        *,
        session_id: str,
        turn_id: str,
        candidates: list[dict[str, Any]],
        recommended_candidate_id: str,
    ) -> TravelCandidateReview:
        owner_user_id = self._owner_user_id(actor)
        try:
            return self.store_for_actor(actor).save_candidate_review(
                owner_user_id, session_id, turn_id, candidates, recommended_candidate_id
            )
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def get_candidate_review(
        self, actor: ActorContext, session_id: str
    ) -> TravelCandidateReview | None:
        owner_user_id = self._owner_user_id(actor)
        try:
            return self.store_for_actor(actor).get_candidate_review(owner_user_id, session_id)
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def select_candidate(
        self, actor: ActorContext, session_id: str, candidate_id: str
    ) -> TravelCandidateReview:
        owner_user_id = self._owner_user_id(actor)
        try:
            return self.store_for_actor(actor).select_candidate(
                owner_user_id, session_id, candidate_id
            )
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def clear_candidate_review(self, actor: ActorContext, session_id: str) -> None:
        """Remove transient candidate state for an actor-owned Session."""

        owner_user_id = self._owner_user_id(actor)
        try:
            self.store_for_actor(actor).clear_candidate_review(owner_user_id, session_id)
        except TravelPlanStoreError as exc:
            raise _application_error_from_store(exc) from exc

    def _require_candidate_selection(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        raw_plan: object,
        selected_candidate_id: str,
        required: bool = False,
    ) -> None:
        review = self.get_candidate_review(actor, session_id)
        if review is None:
            if required:
                raise TravelApplicationError(
                    "TRAVEL_CANDIDATE_SELECTION_REQUIRED",
                    "Prepare candidate options and ask the user to choose before finalizing.",
                    status_code=409,
                )
            return
        if review.status != "selected" or not review.selected_candidate_id:
            raise TravelApplicationError(
                "TRAVEL_CANDIDATE_SELECTION_REQUIRED",
                "Choose a candidate before finalizing the travel plan.",
                status_code=409,
            )
        if selected_candidate_id != review.selected_candidate_id:
            raise TravelApplicationError(
                "TRAVEL_CANDIDATE_SELECTION_MISMATCH",
                "Final plan does not match the selected candidate.",
                status_code=409,
            )
        selected = next(
            (
                item
                for item in review.candidates
                if str(item.get("candidate_id") or "") == review.selected_candidate_id
            ),
            None,
        )
        if not isinstance(selected, dict) or not _plan_matches_candidate(raw_plan, selected):
            raise TravelApplicationError(
                "TRAVEL_CANDIDATE_SELECTION_MISMATCH",
                "Final plan does not preserve the selected candidate itinerary.",
                status_code=409,
            )

    def capability_details(self) -> dict[str, Any]:
        """Return non-secret travel limits for health diagnostics."""

        return {
            "default_mode": self.config.default_mode,
            "max_evidence_items": self.config.max_evidence_items,
            "deep_subagent_count": self.config.deep_subagent_count,
            "xhs_readonly_enabled": self.config.xhs_readonly_enabled,
        }

    def _require_enabled(self) -> None:
        if not self.config.enabled:
            raise TravelApplicationError(
                "TRAVEL_DISABLED",
                "Travel planning is not enabled for this workspace.",
                status_code=503,
            )

    @staticmethod
    def _owner_user_id(actor: ActorContext) -> str:
        owner_user_id = str(actor.user_id or "").strip()
        if not owner_user_id:
            raise TravelApplicationError(
                "TRAVEL_PLAN_ACCESS_DENIED",
                "A database-backed user is required for travel plans.",
                status_code=403,
            )
        return owner_user_id


def _plan_id(value: str) -> str:
    plan_id = str(value or "").strip()
    if not plan_id.startswith("travel-plan-") or len(plan_id) > 100:
        raise TravelApplicationError(
            "TRAVEL_PLAN_NOT_FOUND", "Travel plan was not found.", status_code=404
        )
    return plan_id


def _application_error_from_store(exc: TravelPlanStoreError) -> TravelApplicationError:
    status = 404 if exc.code in {"TRAVEL_PLAN_NOT_FOUND", "TRAVEL_CANDIDATE_REVIEW_NOT_FOUND"} else 403 if exc.code == "TRAVEL_PLAN_ACCESS_DENIED" else 400 if exc.code in {"TRAVEL_CANDIDATE_REVIEW_INVALID", "TRAVEL_CANDIDATE_SELECTION_INVALID"} else 500
    return TravelApplicationError(exc.code, exc.message, status_code=status)


def _plan_matches_candidate(raw_plan: object, candidate: dict[str, Any]) -> bool:
    if not isinstance(raw_plan, dict):
        return False
    plan_days = raw_plan.get("days")
    candidate_days = candidate.get("days")
    if not isinstance(plan_days, list) or not isinstance(candidate_days, list):
        return False
    if len(plan_days) != len(candidate_days):
        return False
    for planned, expected in zip(plan_days, candidate_days, strict=True):
        if not isinstance(planned, dict) or not isinstance(expected, dict):
            return False
        if planned.get("date") != expected.get("date"):
            return False
        if str(planned.get("city_or_area") or "").strip() != str(expected.get("city_or_area") or "").strip():
            return False
        activities = planned.get("activities")
        planned_places = {
            str(item.get("place") or "").strip()
            for item in activities
            if isinstance(item, dict)
        } if isinstance(activities, list) else set()
        expected_places = {str(item).strip() for item in expected.get("places", [])}
        if not expected_places.issubset(planned_places):
            return False
    raw_budget = raw_plan.get("budget")
    expected_budget = candidate.get("budget")
    if not isinstance(raw_budget, dict) or not isinstance(expected_budget, dict):
        return False
    budget_matches = all(
        abs(float(raw_budget.get(key, -1)) - float(expected_budget.get(key, -2))) < 0.01
        for key in ("lower", "expected", "upper")
    )
    route_minutes = 0.0
    route_distance = 0.0
    for day in plan_days:
        segments = day.get("route_segments") if isinstance(day, dict) else None
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            route_minutes += float(segment.get("duration") or 0)
            route_distance += float(segment.get("distance") or 0)
    return (
        budget_matches
        and abs(route_minutes - float(candidate.get("route_minutes") or 0)) < 0.01
        and abs(route_distance - float(candidate.get("route_distance_km") or 0)) < 0.01
    )
