"""Internal travel-domain Tools exposed through the ordinary ToolProvider boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.applications.travel.presentation import travel_plan_summary
from agent.applications.travel.requirements import TravelRequirementDraft
from agent.applications.travel.service import TravelApplicationError, TravelApplicationService
from agent.protocols.tool import ToolExecutionContext, ToolResult
from agent.tools.base import BaseTool

_TRAVEL_REQUEST_PROPERTIES = {
    "schema_version": {"type": "string", "const": "1"},
    "origin": {"type": "string"},
    "destinations": {"type": "array", "items": {"type": "string"}},
    "start_date": {"type": "string", "format": "date"},
    "end_date": {"type": "string", "format": "date"},
    "date_flexibility": {"type": "string"},
    "duration_days": {"type": "integer", "minimum": 1, "maximum": 60},
    "travellers": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"type": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["type", "count"],
            "additionalProperties": False,
        },
    },
    "budget_total_cny": {"type": ["number", "null"]},
    "transport_preferences": {"type": "array", "items": {"type": "string"}},
    "stay_preferences": {"type": "array", "items": {"type": "string"}},
    "interest_tags": {"type": "array", "items": {"type": "string"}},
    "pace": {"type": "string", "enum": ["relaxed", "balanced", "intensive"]},
    "hard_constraints": {"type": "array", "items": {"type": "string"}},
    "soft_preferences": {"type": "array", "items": {"type": "string"}},
    "planning_mode": {"type": "string", "enum": ["quick", "deep"]},
}
_TRAVEL_REQUEST_REQUIRED = [
    "origin", "destinations", "start_date", "end_date", "duration_days", "travellers"
]
_EVIDENCE_PROPERTIES = {
    "evidence_id": {"type": "string"},
    "source_type": {
        "type": "string",
        "enum": [
            "official_api", "live_query", "official_page", "web_article",
            "social_post", "model_estimate",
        ],
    },
    "provider": {"type": "string"},
    "title": {"type": "string"},
    "source_url": {"type": "string"},
    "published_at": {"type": "string"},
    "retrieved_at": {"type": "string"},
    "data_as_of": {"type": "string"},
    "excerpt": {"type": "string"},
    "facts": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "freshness": {
        "type": "string", "enum": ["live", "snapshot", "historical", "estimate", "unknown"]
    },
    "content_hash": {"type": "string"},
}
_EVIDENCE_REQUIRED = [
    "evidence_id", "source_type", "provider", "title", "source_url", "retrieved_at", "freshness"
]
_LOCATION_SCHEMA = {
    "type": "object",
    "properties": {"longitude": {"type": "number"}, "latitude": {"type": "number"}},
    "required": ["longitude", "latitude"],
    "additionalProperties": False,
}
_ACTIVITY_SCHEMA = {
    "type": "object",
    "properties": {
        "start": {"type": "string"},
        "end": {"type": "string"},
        "place": {"type": "string"},
        "reason": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "opening_hours": {"type": "string"},
        "location": _LOCATION_SCHEMA,
    },
    "required": ["start", "end", "place", "reason", "location"],
    "additionalProperties": False,
}
_ROUTE_SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string"},
        "from": {"type": "string"},
        "to": {"type": "string"},
        "duration": {"type": "number"},
        "distance": {"type": "number"},
        "source": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "path": {
            "type": "array",
            "items": _LOCATION_SCHEMA,
        },
    },
    "required": ["mode", "from", "to", "duration", "distance", "source"],
    "additionalProperties": False,
}
_DAY_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "format": "date"},
        "city_or_area": {"type": "string"},
        "activities": {"type": "array", "items": _ACTIVITY_SCHEMA},
        "route_segments": {"type": "array", "items": _ROUTE_SEGMENT_SCHEMA},
        "meal_suggestions": {"type": "array", "items": {"type": "string"}},
        "daily_budget": {"type": "number"},
        "weather_adjustment": {"type": "string"},
        "fallback_plan": {"type": "string"},
        "intensity_score": {"type": "number", "minimum": 0, "maximum": 10},
    },
    "required": ["date", "city_or_area", "activities"],
    "additionalProperties": False,
}
_BUDGET_SCHEMA = {
    "type": "object",
    "properties": {
        "lower": {"type": "number"},
        "expected": {"type": "number"},
        "upper": {"type": "number"},
        "items": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["lower", "expected", "upper", "items"],
    "additionalProperties": False,
}

_INTAKE_PATCH_PROPERTIES = {
    "origin": {"type": "string", "maxLength": 120},
    "destinations": {
        "type": "array",
        "maxItems": 8,
        "items": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "start_date": {"type": "string", "maxLength": 10},
    "end_date": {"type": "string", "maxLength": 10},
    "traveller_type": {"type": "string", "maxLength": 40},
    "traveller_count": {"type": ["integer", "null"], "minimum": 1, "maximum": 50},
    "budget_total_cny": {"type": ["number", "null"], "minimum": 100, "maximum": 10_000_000},
    "budget_level": {"type": "string", "enum": ["", "economy", "balanced", "comfortable"]},
    "transport_preferences": {
        "type": "array",
        "maxItems": 12,
        "items": {"type": "string", "minLength": 1, "maxLength": 100},
    },
    "stay_preferences": {
        "type": "array",
        "maxItems": 12,
        "items": {"type": "string", "minLength": 1, "maxLength": 160},
    },
    "interest_tags": {
        "type": "array",
        "maxItems": 20,
        "items": {"type": "string", "minLength": 1, "maxLength": 80},
    },
    "pace": {"type": "string", "enum": ["", "relaxed", "balanced", "intensive"]},
    "planning_mode": {"type": "string", "enum": ["", "quick", "deep"]},
    "hard_constraints": {
        "type": "array",
        "maxItems": 20,
        "items": {"type": "string", "minLength": 1, "maxLength": 300},
    },
}
_INTAKE_FIELDS = tuple(_INTAKE_PATCH_PROPERTIES)


class UpdateTravelDraftTool(BaseTool):
    """Merge one model-understood intake patch into actor-owned Session metadata."""

    name = "update_travel_draft"
    description = (
        "Record only travel conditions explicitly supplied or corrected by the user, then return "
        "the complete validated draft, missing core fields, and readiness. Use an empty patch for "
        "in-scope conversation that does not change conditions."
    )
    parameters = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "object",
                "properties": _INTAKE_PATCH_PROPERTIES,
                "additionalProperties": False,
            },
            "clear_fields": {
                "type": "array",
                "maxItems": len(_INTAKE_FIELDS),
                "items": {"type": "string", "enum": list(_INTAKE_FIELDS)},
            },
        },
        "required": ["patch"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, sessions):
        super().__init__(workspace)
        self.sessions = sessions

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return _intake_error(self.name, "TRAVEL_INTAKE_CONTEXT_REQUIRED", "Travel intake requires trusted turn context.")

    def execute_with_context(
        self, args: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        guard = _intake_context_error(self.name, self.sessions, context)
        if guard is not None:
            return guard
        patch = args.get("patch") if isinstance(args, dict) else None
        clear_fields = args.get("clear_fields", []) if isinstance(args, dict) else []
        if not isinstance(patch, dict) or set(patch) - set(_INTAKE_FIELDS):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "Travel draft patch is invalid.")
        if not isinstance(clear_fields, list) or any(item not in _INTAKE_FIELDS for item in clear_fields):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "Travel draft clear_fields is invalid.")
        if set(patch) & set(clear_fields):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "A field cannot be updated and cleared together.")
        state = self.sessions.load(context.session_id)
        merged = _complete_intake_draft(state.metadata.get("travel_draft"))
        for field in clear_fields:
            merged[field] = _empty_intake_value(field)
        merged.update(patch)
        merged["intent"] = "travel_requirement"
        merged["intent_topic"] = ""
        try:
            draft = TravelRequirementDraft.from_dict(merged).to_dict()
        except (TravelApplicationError, TypeError, ValueError):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "Travel draft patch did not pass validation.")
        missing = _intake_missing_fields(draft)
        changed_fields = sorted({*patch, *clear_fields})
        metadata: dict[str, Any] = {
            "travel_phase": "intake",
            "travel_draft": draft,
            "travel_draft_version": 1,
            "travel_intake_turn_ids": _intake_turn_ids(state.metadata, context.turn_id),
        }
        if changed_fields:
            metadata["travel_handoff_question"] = ""
            metadata["travel_handoff_topic"] = ""
        title = _intake_title(draft)
        if title:
            metadata["title"] = title
        self.sessions.update_metadata(context.session_id, metadata)
        detail_data = {
            "draft": draft,
            "missing_fields": missing,
            "ready": not missing,
            "changed_fields": changed_fields,
        }
        if context.runtime_events is not None:
            context.runtime_events.emit(
                "travel.intake_draft_updated",
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                display={"visibility": "internal"},
                ui_metadata={"detail_type": "travel_intake_draft", "detail_data": detail_data},
                metadata={"ready": not missing, "missing_count": len(missing)},
            )
        return ToolResult(
            output=json.dumps(
                {"status": "success", "code": "OK", **detail_data},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={"code": "OK", "tool_name": self.name, "ready": not missing},
        )


class OfferMainChatHandoffTool(BaseTool):
    """Publish a safe handoff for a clearly non-travel user question."""

    name = "offer_main_chat_handoff"
    description = (
        "Offer to carry a clearly non-travel question back to the main chat. Do not answer the "
        "question itself and do not use this for travel-related discussion."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1, "maxLength": 2000},
            "topic": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "required": ["question", "topic"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, sessions):
        super().__init__(workspace)
        self.sessions = sessions

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return _intake_error(self.name, "TRAVEL_INTAKE_CONTEXT_REQUIRED", "Travel intake requires trusted turn context.")

    def execute_with_context(
        self, args: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        guard = _intake_context_error(self.name, self.sessions, context)
        if guard is not None:
            return guard
        question = str(args.get("question") or "").strip() if isinstance(args, dict) else ""
        topic = str(args.get("topic") or "").strip() if isinstance(args, dict) else ""
        if not question or len(question) > 2000 or not topic or len(topic) > 80:
            return _intake_error(self.name, "TRAVEL_HANDOFF_INVALID", "Main chat handoff is invalid.")
        state = self.sessions.load(context.session_id)
        self.sessions.update_metadata(
            context.session_id,
            {
                "travel_phase": "intake",
                "travel_intake_turn_ids": _intake_turn_ids(state.metadata, context.turn_id),
                "travel_handoff_question": question,
                "travel_handoff_topic": topic,
            },
        )
        if context.runtime_events is not None:
            context.runtime_events.emit(
                "travel.main_chat_handoff",
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                display={"visibility": "internal"},
                ui_metadata={
                    "detail_type": "travel_main_chat_handoff",
                    "detail_data": {"question": question, "topic": topic},
                },
                metadata={"topic": topic},
            )
        return ToolResult(
            output=json.dumps(
                {"status": "handoff_offered", "code": "OK", "topic": topic},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={"code": "OK", "tool_name": self.name},
        )


class ConfirmAndStartTravelPlanningTool(BaseTool):
    """Commit explicit user confirmation and open the formal planning phase."""

    name = "confirm_and_start_travel_planning"
    description = (
        "Confirm a complete server-side travel draft and start formal planning when the user "
        "explicitly asks to confirm, start, execute, or begin planning. Do not call while required "
        "travel fields are missing or when the user is only discussing possibilities."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(
        self,
        workspace: Path | str,
        sessions,
        confirm_planning: Callable[[object, str, dict[str, object]], dict[str, str]],
    ):
        super().__init__(workspace)
        self.sessions = sessions
        self.confirm_planning = confirm_planning

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return _intake_error(
            self.name,
            "TRAVEL_INTAKE_CONTEXT_REQUIRED",
            "Travel planning confirmation requires trusted turn context.",
        )

    def execute_with_context(
        self, args: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        guard = _intake_context_error(self.name, self.sessions, context)
        if guard is not None:
            return guard
        if not isinstance(args, dict) or args:
            return _intake_error(
                self.name,
                "TRAVEL_PLANNING_CONFIRMATION_INVALID",
                "Travel planning confirmation does not accept arguments.",
            )
        state = self.sessions.load(context.session_id)
        draft = state.metadata.get("travel_draft")
        if not isinstance(draft, dict):
            return _intake_error(
                self.name,
                "TRAVEL_REQUIREMENTS_INCOMPLETE",
                "Travel requirements are incomplete.",
            )
        try:
            payload = self.confirm_planning(context.actor, context.session_id, dict(draft))
        except TravelApplicationError as exc:
            return _intake_error(self.name, exc.code, exc.message)
        if context.runtime_events is not None:
            context.runtime_events.emit(
                "travel.planning_confirmed",
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                display={"visibility": "internal"},
                ui_metadata={
                    "detail_type": "travel_planning_confirmed",
                    "detail_data": {"phase": "planning"},
                },
                metadata={"phase": "planning"},
            )
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            metadata={"code": "OK", "tool_name": self.name, "phase": "planning"},
        )


class FinalizeTravelPlanTool(BaseTool):
    """Validate and persist a complete TravelPlanV1 for the current actor."""

    name = "finalize_travel_plan"
    description = (
        "Validate and save a complete sourced TravelPlanV1 for the current user. "
        "Call only after travel research, optimization, and quality gates pass."
    )
    parameters = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "object",
                "description": "Complete TravelPlanV1; owner_user_id and plan_id are reassigned safely.",
                "properties": {
                    "schema_version": {"type": "string", "const": "1"},
                    "plan_id": {"type": "string"},
                    "owner_user_id": {"type": "string"},
                    "request": {
                        "type": "object",
                        "properties": _TRAVEL_REQUEST_PROPERTIES,
                        "required": _TRAVEL_REQUEST_REQUIRED,
                        "additionalProperties": False,
                    },
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "freshness_summary": {},
                    "transport_options": {"type": "array", "items": {"type": "object"}},
                    "stay_recommendations": {"type": "array", "items": {"type": "object"}},
                    "days": {"type": "array", "items": _DAY_SCHEMA},
                    "budget": _BUDGET_SCHEMA,
                    "weather_summary": {"type": "array", "items": {"type": "object"}},
                    "fallbacks": {"type": "array", "items": {"type": "string"}},
                    "avoidance_tips": {"type": "array", "items": {"type": "string"}},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": _EVIDENCE_PROPERTIES,
                            "required": _EVIDENCE_REQUIRED,
                            "additionalProperties": False,
                        },
                    },
                    "unknowns": {"type": "array", "items": {"type": "string"}},
                    "generated_at": {"type": "string"},
                },
                "required": [
                    "schema_version",
                    "request",
                    "assumptions",
                    "freshness_summary",
                    "transport_options",
                    "stay_recommendations",
                    "days",
                    "budget",
                    "weather_summary",
                    "fallbacks",
                    "avoidance_tips",
                    "evidence",
                    "unknowns",
                    "generated_at",
                ],
                "additionalProperties": False,
            },
            "selected_candidate_id": {
                "type": "string",
                "description": "Candidate confirmed by the user when candidate review was required.",
                "maxLength": 100,
            },
        },
        "required": ["plan"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, service: TravelApplicationService):
        super().__init__(workspace)
        self.service = service

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return ToolResult(
            output="Travel plan finalization requires trusted turn context.",
            is_error=True,
            metadata={"code": "TRAVEL_PLAN_ACCESS_DENIED", "tool_name": self.name},
        )

    def execute_with_context(
        self,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Use trusted actor/session/turn facts and emit one plan-ready event."""

        if not isinstance(args, dict) or set(args) - {"plan", "selected_candidate_id"} or "plan" not in args:
            return _error("TRAVEL_PLAN_SCHEMA_INVALID", "A single plan object is required.")
        if context.channel == "travel":
            research_error = _research_completion_error(
                self.service.source_ledger.snapshot(context.session_id),
                args["plan"],
            )
            if research_error is not None:
                return research_error
        try:
            plan = self.service.finalize(
                context.actor,
                args["plan"],
                source_session_id=context.session_id,
                source_turn_id=context.turn_id,
                selected_candidate_id=str(args.get("selected_candidate_id") or "").strip(),
                require_candidate_review=context.channel == "travel",
            )
        except TravelApplicationError as exc:
            metadata = {"code": exc.code, "tool_name": self.name}
            if exc.field:
                metadata["field"] = exc.field
            output = f"{exc.field}: {exc.message}" if exc.field else exc.message
            return ToolResult(output=output, is_error=True, metadata=metadata)
        plan_id = str(plan.data["plan_id"])
        self.service.source_ledger.clear(context.session_id)
        view_url = f"/travel?plan={plan_id}"
        if context.runtime_events is not None:
            context.runtime_events.emit(
                "travel.plan_ready",
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                display={"title": "旅行计划已生成", "detail": "正在打开结构化旅行计划"},
                metadata={"plan_id": plan_id},
            )
        payload = {
            "status": "success",
            "code": "OK",
            "plan_id": plan_id,
            "view_url": view_url,
            "summary": travel_plan_summary(plan),
        }
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            metadata={"code": "OK", "tool_name": self.name, "plan_id": plan_id},
        )


class RequestTravelClarificationTool(BaseTool):
    """Publish one bounded set of user questions instead of ending silently."""

    name = "request_travel_clarification"
    description = (
        "Pause travel planning only when required user-owned decisions are missing. "
        "Provide every required question together; do not use for tool or agent failures."
    )
    parameters = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 160},
                "minItems": 1,
                "maxItems": 6,
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    }

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return ToolResult(
            output="Travel clarification requires trusted turn context.",
            is_error=True,
            metadata={"code": "TRAVEL_PLAN_ACCESS_DENIED", "tool_name": self.name},
        )

    def execute_with_context(
        self,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        questions = args.get("questions") if isinstance(args, dict) else None
        if (
            not isinstance(questions, list)
            or not 1 <= len(questions) <= 6
            or any(
                not isinstance(question, str)
                or not question.strip()
                or len(question.strip()) > 160
                for question in questions
            )
        ):
            return ToolResult(
                output="Provide between one and six short clarification questions.",
                is_error=True,
                metadata={"code": "TRAVEL_CLARIFICATION_INVALID", "tool_name": self.name},
            )
        normalized = [question.strip() for question in questions]
        if context.runtime_events is not None:
            context.runtime_events.emit(
                "travel.clarification_required",
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                display={"title": "还需要确认一些旅行信息", "detail": "补充后会继续为你规划"},
                ui_metadata={
                    "detail_type": "summary",
                    "detail_data": {"questions": normalized},
                },
                metadata={"question_count": len(normalized)},
            )
        return ToolResult(
            output=json.dumps(
                {"status": "waiting_for_user", "code": "TRAVEL_CLARIFICATION_REQUIRED"},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={"code": "TRAVEL_CLARIFICATION_REQUIRED", "tool_name": self.name},
        )


class RequestTravelCandidateReviewTool(BaseTool):
    """Persist feasible candidate summaries and pause for a real user decision."""

    name = "request_travel_candidate_review"
    description = (
        "Pause after a successful travel optimizer run when two or more feasible candidates exist. "
        "Pass only the optimizer's feasible_candidates summaries and recommended candidate id."
    )
    parameters = {
        "type": "object",
        "properties": {
            "recommended_candidate_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "candidates": {
                "type": "array",
                "minItems": 2,
                "maxItems": 5,
                "items": {"type": "object"},
            },
        },
        "required": ["recommended_candidate_id", "candidates"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Path | str, service: TravelApplicationService):
        super().__init__(workspace)
        self.service = service

    def _execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return ToolResult(
            output="Travel candidate review requires trusted turn context.",
            is_error=True,
            metadata={"code": "TRAVEL_PLAN_ACCESS_DENIED", "tool_name": self.name},
        )

    def execute_with_context(
        self, args: dict[str, Any], context: ToolExecutionContext
    ) -> ToolResult:
        recommended = str(args.get("recommended_candidate_id") or "").strip() if isinstance(args, dict) else ""
        raw_candidates = args.get("candidates") if isinstance(args, dict) else None
        try:
            candidates = _candidate_summaries(raw_candidates)
            review = self.service.save_candidate_review(
                context.actor,
                session_id=context.session_id,
                turn_id=context.turn_id,
                candidates=candidates,
                recommended_candidate_id=recommended,
            )
        except (ValueError, TravelApplicationError) as exc:
            code = exc.code if isinstance(exc, TravelApplicationError) else "TRAVEL_CANDIDATE_REVIEW_INVALID"
            message = exc.message if isinstance(exc, TravelApplicationError) else str(exc)
            return ToolResult(
                output=message,
                is_error=True,
                metadata={"code": code, "tool_name": self.name},
            )
        detail_data = review.to_dict()
        detail_data.pop("created_at", None)
        detail_data.pop("updated_at", None)
        if context.runtime_events is not None:
            context.runtime_events.emit(
                "travel.candidate_review_required",
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                display={"title": "候选行程已准备好", "detail": "选择一个方案后生成完整旅行计划"},
                ui_metadata={"detail_type": "travel_candidates", "detail_data": detail_data},
                metadata={"candidate_count": len(candidates)},
            )
        return ToolResult(
            output=json.dumps(
                {"status": "waiting_for_user", "code": "TRAVEL_CANDIDATE_REVIEW_REQUIRED"},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={"code": "TRAVEL_CANDIDATE_REVIEW_REQUIRED", "tool_name": self.name},
        )


def _intake_context_error(
    tool_name: str, sessions, context: ToolExecutionContext
) -> ToolResult | None:
    if context.channel != "travel":
        return _intake_error(
            tool_name,
            "TRAVEL_INTAKE_ACCESS_DENIED",
            "Travel intake tools are available only in a travel Session.",
        )
    state = sessions.load(context.session_id)
    phase = str(state.metadata.get("travel_phase") or "intake")
    if phase != "intake":
        return _intake_error(
            tool_name,
            "TRAVEL_INTAKE_PHASE_CLOSED",
            "Travel intake is already confirmed and cannot be changed by this tool.",
        )
    return None


def _intake_error(tool_name: str, code: str, message: str) -> ToolResult:
    return ToolResult(
        output=message,
        is_error=True,
        metadata={"code": code, "tool_name": tool_name},
    )


def _complete_intake_draft(value: object) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "intent": "travel_requirement",
        "intent_topic": "",
        "origin": "",
        "destinations": [],
        "start_date": "",
        "end_date": "",
        "traveller_type": "",
        "traveller_count": None,
        "budget_total_cny": None,
        "budget_level": "",
        "transport_preferences": [],
        "stay_preferences": [],
        "interest_tags": [],
        "pace": "",
        "planning_mode": "",
        "hard_constraints": [],
    }
    if not isinstance(value, dict):
        return empty
    for field in _INTAKE_FIELDS:
        if field in value:
            empty[field] = value[field]
    return empty


def _empty_intake_value(field: str) -> Any:
    if field in {
        "destinations",
        "transport_preferences",
        "stay_preferences",
        "interest_tags",
        "hard_constraints",
    }:
        return []
    if field in {"traveller_count", "budget_total_cny"}:
        return None
    return ""


def _intake_missing_fields(draft: dict[str, Any]) -> list[str]:
    checks = (
        ("origin", "出发地"),
        ("destinations", "目的地"),
        ("start_date", "开始日期"),
        ("end_date", "结束日期"),
        ("traveller_count", "人数"),
    )
    return [label for field, label in checks if not draft.get(field)]


def _intake_turn_ids(metadata: dict[str, Any], turn_id: str) -> list[str]:
    existing = metadata.get("travel_intake_turn_ids")
    values = [str(item) for item in existing if str(item).strip()] if isinstance(existing, list) else []
    normalized = str(turn_id or "").strip()
    if normalized and normalized not in values:
        values.append(normalized)
    return values[-40:]


def _intake_title(draft: dict[str, Any]) -> str:
    origin = str(draft.get("origin") or "").strip()
    destinations = draft.get("destinations")
    destination = str(destinations[0]).strip() if isinstance(destinations, list) and destinations else ""
    if origin and destination:
        return f"{origin} → {destination}"
    if destination:
        return f"{destination}旅行计划"
    return ""


def _candidate_summaries(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 2 <= len(value) <= 5:
        raise ValueError("Provide between two and five feasible candidates.")
    allowed = {
        "candidate_id", "recommended", "score", "days", "budget", "route_minutes",
        "route_distance_km", "daily_intensity_scores", "evidence_coverage", "warnings",
    }
    normalized: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("Candidate summary contains unsupported fields.")
        candidate_id = str(raw.get("candidate_id") or "").strip()
        if not candidate_id or len(candidate_id) > 100 or candidate_id in identifiers:
            raise ValueError("Candidate id is invalid.")
        identifiers.add(candidate_id)
        days = raw.get("days")
        if not isinstance(days, list) or not 1 <= len(days) <= 60:
            raise ValueError("Candidate days are invalid.")
        safe_days = []
        for day in days:
            if not isinstance(day, dict):
                raise ValueError("Candidate day is invalid.")
            places = day.get("places")
            if not isinstance(places, list):
                raise ValueError("Candidate places are invalid.")
            safe_days.append({
                "date": str(day.get("date") or "")[:10],
                "city_or_area": str(day.get("city_or_area") or "")[:120],
                "places": [str(item)[:100] for item in places[:6] if str(item).strip()],
            })
        budget = raw.get("budget") if isinstance(raw.get("budget"), dict) else {}
        normalized.append({
            "candidate_id": candidate_id,
            "recommended": bool(raw.get("recommended")),
            "score": float(raw.get("score") or 0),
            "days": safe_days,
            "budget": {key: float(budget.get(key) or 0) for key in ("lower", "expected", "upper")},
            "route_minutes": float(raw.get("route_minutes") or 0),
            "route_distance_km": float(raw.get("route_distance_km") or 0),
            "daily_intensity_scores": [float(item) for item in (raw.get("daily_intensity_scores") or [])[:60]],
            "evidence_coverage": float(raw.get("evidence_coverage") or 0),
            "warnings": [str(item)[:80] for item in (raw.get("warnings") or [])[:10]],
        })
    return normalized

def _error(code: str, message: str) -> ToolResult:
    return ToolResult(
        output=message,
        is_error=True,
        metadata={"code": code, "tool_name": FinalizeTravelPlanTool.name},
    )


def _research_completion_error(snapshot, raw_plan: object) -> ToolResult | None:
    if not snapshot.expected:
        return _error(
            "TRAVEL_RESEARCH_INCOMPLETE",
            "No travel data sources are currently available; do not finalize a sourced plan.",
        )
    if snapshot.missing_attempts:
        return _error(
            "TRAVEL_RESEARCH_INCOMPLETE",
            "Configured travel source categories have not all been queried; continue source research before finalizing.",
        )
    if snapshot.retry_required:
        return _error(
            "TRAVEL_RESEARCH_INCOMPLETE",
            "A travel search source returned no usable results and still requires one narrower retry before finalizing.",
        )
    if not snapshot.successful:
        return _error(
            "TRAVEL_EVIDENCE_INSUFFICIENT",
            "No external travel source returned usable data; continue recovery or report a stable source failure.",
        )
    evidence = raw_plan.get("evidence") if isinstance(raw_plan, dict) else None
    has_external = isinstance(evidence, list) and any(
        isinstance(item, dict) and item.get("source_type") != "model_estimate"
        for item in evidence
    )
    if not has_external:
        return _error(
            "TRAVEL_EVIDENCE_INSUFFICIENT",
            "The plan contains no external evidence even though sources were queried; normalize the verified source results before finalizing.",
        )
    return None
