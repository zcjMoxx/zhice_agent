"""Internal travel-domain Tools exposed through the ordinary ToolProvider boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from agent.applications.travel.drafts import (
    MAX_REPAIR_OPERATIONS,
    TravelDraftRepairError,
    apply_travel_plan_repairs,
)
from agent.applications.travel.presentation import travel_plan_summary
from agent.applications.travel.requirements import TravelRequirementDraft
from agent.applications.travel.service import (
    TravelApplicationError,
    TravelApplicationService,
    reconcile_transport_option_sources,
)
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

_SENSITIVE_EVIDENCE_URL_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)
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
_TRANSIT_LEG_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string"},
        "line_name": {"type": "string"},
        "departure_stop": {"type": "string"},
        "arrival_stop": {"type": "string"},
        "via_stops": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mode", "line_name", "departure_stop", "arrival_stop", "via_stops"],
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
        "transit_legs": {"type": "array", "items": _TRANSIT_LEG_SCHEMA},
        "walking_distance": {
            "anyOf": [
                {"type": "number", "minimum": 0, "maximum": 50000},
                {"type": "string", "pattern": r"^\d+(?:\.\d+)?$"},
            ]
        },
        "fare_cny": {"type": ["number", "null"], "minimum": 0},
    },
    "required": ["mode", "from", "to", "duration", "distance", "source"],
    "additionalProperties": False,
}
_TRANSPORT_OPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "mode": {"type": "string", "minLength": 1, "maxLength": 100},
        "from": {"type": "string"},
        "to": {"type": "string"},
        "service_name": {"type": "string"},
        "departure": {"type": "string"},
        "arrival": {"type": "string"},
        "duration_minutes": {"type": "number", "minimum": 0},
        "seat": {"type": "string"},
        "price_cny_per_person": {"type": ["number", "null"], "minimum": 0},
        "price_cny_total": {"type": ["number", "null"], "minimum": 0},
        "source": {"type": "string"},
        "summary": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "mode", "evidence_ids"],
    "additionalProperties": False,
}
_STAY_RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "hotel_name": {"type": "string"},
        "address": {"type": "string"},
        "area": {"type": "string"},
        "location": _LOCATION_SCHEMA,
        "check_in": {"type": "string", "format": "date"},
        "check_out": {"type": "string", "format": "date"},
        "nights": {"type": "integer", "minimum": 1, "maximum": 60},
        "observed_price_per_night_cny": {"type": ["number", "null"], "minimum": 0},
        "planning_estimate_per_night_cny": {"type": ["number", "null"], "minimum": 0},
        "price_status": {
            "type": "string",
            "enum": ["live_observed", "snapshot_observed", "planning_estimate", "unavailable"],
        },
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "price_source_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": [
        "hotel_name", "address", "area", "location", "check_in", "check_out",
        "nights", "observed_price_per_night_cny", "planning_estimate_per_night_cny",
        "price_status", "evidence_ids", "price_source_evidence_ids", "reason",
    ],
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
TRAVEL_INTAKE_DRAFT_VERSION = 3
_INTAKE_PLACEHOLDERS = frozenset(
    {"string", "unknown", "none", "null", "n/a", "未提供", "待定", "示例"}
)


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
            "location_clarifications": {
                "type": "array",
                "maxItems": 4,
                "items": {"type": "string", "minLength": 1, "maxLength": 240},
                "description": (
                    "All unresolved questions needed to identify an origin or destination uniquely. "
                    "Pass [] only after the user explicitly resolves every location ambiguity."
                ),
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
        raw_location_clarifications = (
            args.get("location_clarifications") if isinstance(args, dict) else None
        )
        if not isinstance(patch, dict) or set(patch) - set(_INTAKE_FIELDS):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "Travel draft patch is invalid.")
        if not isinstance(clear_fields, list) or any(item not in _INTAKE_FIELDS for item in clear_fields):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "Travel draft clear_fields is invalid.")
        if set(patch) & set(clear_fields):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "A field cannot be updated and cleared together.")
        if raw_location_clarifications is not None and (
            not isinstance(raw_location_clarifications, list)
            or len(raw_location_clarifications) > 4
            or any(
                not isinstance(item, str) or not item.strip() or len(item.strip()) > 240
                for item in raw_location_clarifications
            )
        ):
            return _intake_error(
                self.name,
                "TRAVEL_LOCATION_CLARIFICATION_INVALID",
                "Travel location clarifications are invalid.",
            )
        state = self.sessions.load(context.session_id)
        user_text = _intake_turn_user_text(state.messages, context.turn_id)
        patch = _ground_intake_patch(patch, user_text)
        effective_patch = {
            field: value
            for field, value in patch.items()
            if value != _empty_intake_value(field)
        }
        merged = recover_intake_draft(state.metadata, state.messages)
        for field in clear_fields:
            merged[field] = _empty_intake_value(field)
        merged.update(effective_patch)
        merged["intent"] = "travel_requirement"
        merged["intent_topic"] = ""
        try:
            draft = TravelRequirementDraft.from_dict(merged).to_dict()
        except (TravelApplicationError, TypeError, ValueError):
            return _intake_error(self.name, "TRAVEL_DRAFT_INVALID", "Travel draft patch did not pass validation.")
        missing = _intake_missing_fields(draft)
        previous_location_clarifications = state.metadata.get(
            "travel_location_clarifications", []
        )
        if not isinstance(previous_location_clarifications, list):
            previous_location_clarifications = []
        location_clarifications = (
            [item.strip() for item in raw_location_clarifications]
            if raw_location_clarifications is not None
            else [str(item).strip() for item in previous_location_clarifications if str(item).strip()][:4]
        )
        ready = not missing and not location_clarifications
        changed_fields = sorted({*effective_patch, *clear_fields})
        metadata: dict[str, Any] = {
            "travel_phase": "intake",
            "travel_draft": draft,
            "travel_draft_version": TRAVEL_INTAKE_DRAFT_VERSION,
            "travel_intake_turn_ids": _intake_turn_ids(state.metadata, context.turn_id),
            "travel_location_clarifications": location_clarifications,
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
            "location_clarifications": location_clarifications,
            "ready": ready,
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
                metadata={"ready": ready, "missing_count": len(missing), "location_clarification_count": len(location_clarifications)},
            )
        return ToolResult(
            output=json.dumps(
                {"status": "success", "code": "OK", **detail_data},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={"code": "OK", "tool_name": self.name, "ready": ready},
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
        location_clarifications = state.metadata.get("travel_location_clarifications", [])
        if isinstance(location_clarifications, list) and any(str(item).strip() for item in location_clarifications):
            return _intake_error(
                self.name,
                "TRAVEL_LOCATION_CLARIFICATION_REQUIRED",
                "Clarify the ambiguous origin or destination before formal planning.",
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
    """Validate a first full plan or repair the server-owned failed draft."""

    name = "finalize_travel_plan"
    description = (
        "Validate and save a sourced TravelPlanV1 for the current user. Submit plan only on the "
        "first attempt. After a repair_required error, submit draft_revision plus all needed "
        "JSON Pointer repairs without regenerating the complete plan."
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
                    "transport_options": {"type": "array", "items": _TRANSPORT_OPTION_SCHEMA},
                    "stay_recommendations": {"type": "array", "items": _STAY_RECOMMENDATION_SCHEMA},
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
            "draft_revision": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}$",
                "description": "Current server-owned draft revision returned by a failed attempt.",
            },
            "repairs": {
                "type": "array",
                "minItems": 0,
                "maxItems": MAX_REPAIR_OPERATIONS,
                "description": "All known corrections for the current draft; never resend plan.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["set", "remove"]},
                        "path": {"type": "string", "minLength": 2, "maxLength": 300},
                        "value": {},
                    },
                    "required": ["op", "path"],
                    "additionalProperties": False,
                },
            },
        },
        "oneOf": [
            {
                "required": ["plan"],
                "not": {
                    "anyOf": [
                        {"required": ["draft_revision"]},
                        {"required": ["repairs"]},
                    ]
                },
            },
            {
                "required": ["draft_revision", "repairs"],
                "not": {
                    "anyOf": [
                        {"required": ["plan"]},
                        {"required": ["selected_candidate_id"]},
                    ]
                },
            },
        ],
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

        allowed = {"plan", "selected_candidate_id", "draft_revision", "repairs"}
        if not isinstance(args, dict) or set(args) - allowed:
            return _error("TRAVEL_PLAN_SCHEMA_INVALID", "Finalizer arguments are invalid.")
        has_plan = "plan" in args
        has_repairs = "draft_revision" in args or "repairs" in args
        if has_plan == has_repairs:
            return _error(
                "TRAVEL_PLAN_SCHEMA_INVALID",
                "Submit either the first complete plan or draft_revision with repairs.",
            )
        try:
            durable_draft = self.service.get_plan_draft(context.actor, context.session_id)
        except TravelApplicationError as exc:
            return _application_tool_error(exc)
        cached_attempts = self.service.source_ledger.plan_attempts(context.session_id)
        latest = durable_draft or (cached_attempts[0] if cached_attempts else None)
        selected_candidate_id = str(args.get("selected_candidate_id") or "").strip()
        if has_plan:
            if latest is not None:
                return _draft_error(
                    "TRAVEL_PLAN_DRAFT_EXISTS",
                    "A failed server-owned draft already exists; repair it instead of resending plan.",
                    draft_revision=str(latest.get("draft_revision") or ""),
                )
            raw_plan = args["plan"]
            expected_revision: str | None = None
        else:
            if "selected_candidate_id" in args:
                return _draft_error(
                    "TRAVEL_PLAN_DRAFT_CANDIDATE_MISMATCH",
                    "A repair must reuse the candidate already bound to the server-owned draft.",
                    draft_revision=str((latest or {}).get("draft_revision") or ""),
                )
            if latest is None:
                return _error(
                    "TRAVEL_PLAN_DRAFT_MISSING",
                    "No failed server-owned travel plan draft is available; submit the first complete plan.",
                )
            latest_revision = str(latest.get("draft_revision") or "")
            supplied_revision = str(args.get("draft_revision") or "").strip()
            if supplied_revision != latest_revision:
                return _draft_error(
                    "TRAVEL_PLAN_DRAFT_CONFLICT",
                    "Travel plan draft changed; apply repairs to the latest revision.",
                    draft_revision=latest_revision,
                )
            inherited_candidate_id = str(latest.get("selected_candidate_id") or "").strip()
            selected_candidate_id = inherited_candidate_id
            expected_revision = latest_revision if durable_draft is not None else None
            try:
                raw_plan = apply_travel_plan_repairs(latest["plan"], args.get("repairs"))
            except TravelDraftRepairError as exc:
                return _draft_error(
                    exc.code,
                    exc.message,
                    field=exc.field,
                    draft_revision=latest_revision,
                )
        if isinstance(raw_plan, dict):
            raw_plan = _coerce_plan_route_numeric_strings(raw_plan)
            raw_plan = _sanitize_plan_evidence_urls(raw_plan)
        if isinstance(raw_plan, dict):
            for previous_attempt in self.service.source_ledger.plan_attempts(
                context.session_id
            ):
                previous_plan = previous_attempt.get("plan")
                if not isinstance(previous_plan, dict):
                    continue
                if previous_attempt.get("transit_verified"):
                    raw_plan = _merge_previous_verified_transit(raw_plan, previous_plan)
                if previous_attempt.get("live_weather_verified"):
                    raw_plan = _merge_previous_live_weather(raw_plan, previous_plan)
        if context.channel == "travel":
            if isinstance(raw_plan, dict):
                raw_plan = _merge_ledger_search_evidence(
                    raw_plan,
                    web=self.service.source_ledger.search_evidence(
                        context.session_id, "web"
                    ),
                    social=self.service.source_ledger.search_evidence(
                        context.session_id, "social"
                    ),
                )
                raw_plan = _merge_ledger_structured_results(
                    raw_plan,
                    self.service.source_ledger.structured_results(
                        context.session_id
                    ),
                )
                raw_plan = _reconcile_observed_stay_budget(raw_plan)
                raw_plan = _reconcile_persisted_transport_envelope(raw_plan)
        attempt: dict[str, Any] | None = None
        if isinstance(raw_plan, dict):
            raw_plan = reconcile_transport_option_sources(raw_plan)
            try:
                attempt = self.service.save_plan_draft(
                    context.actor,
                    context.session_id,
                    raw_plan,
                    selected_candidate_id=selected_candidate_id,
                    expected_revision=expected_revision,
                )
            except TravelApplicationError as exc:
                if exc.code == "TRAVEL_PLAN_DRAFT_CONFLICT":
                    try:
                        newest = self.service.get_plan_draft(context.actor, context.session_id)
                    except TravelApplicationError as refresh_exc:
                        return _application_tool_error(refresh_exc)
                    return _draft_error(
                        exc.code,
                        exc.message,
                        draft_revision=str((newest or {}).get("draft_revision") or ""),
                    )
                return _application_tool_error(exc)
            structural_issues = _plan_schema_issues(raw_plan)
            if structural_issues:
                return _repair_required_payload(
                    code="TRAVEL_PLAN_SCHEMA_INVALID",
                    message="Travel plan draft has structural issues.",
                    issues=structural_issues,
                    attempt=attempt,
                )
        if context.channel == "travel":
            research_error = _research_completion_error(
                self.service.source_ledger.snapshot(context.session_id),
                raw_plan,
            )
            if research_error is not None:
                if attempt is not None:
                    return _research_error_with_draft(research_error, attempt)
                return research_error
        try:
            plan = self.service.finalize(
                context.actor,
                raw_plan,
                source_session_id=context.session_id,
                source_turn_id=context.turn_id,
                selected_candidate_id=selected_candidate_id,
                require_candidate_review=context.channel == "travel",
                expected_draft_revision=(
                    str(attempt.get("draft_revision") or "")
                    if attempt is not None
                    else None
                ),
            )
        except TravelApplicationError as exc:
            if exc.code == "TRAVEL_PLAN_DRAFT_CONFLICT":
                try:
                    newest = self.service.get_plan_draft(context.actor, context.session_id)
                except TravelApplicationError as refresh_exc:
                    return _application_tool_error(refresh_exc)
                return _draft_error(
                    exc.code,
                    exc.message,
                    draft_revision=str((newest or {}).get("draft_revision") or ""),
                )
            if attempt is not None and exc.status_code < 500:
                return _repair_required_error(exc, raw_plan, attempt)
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


def _sanitize_plan_evidence_urls(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Remove credential query fields before strict plan validation and persistence."""

    sanitized = deepcopy(raw_plan)
    evidence = sanitized.get("evidence")
    if not isinstance(evidence, list):
        return sanitized
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("source_url"), str):
            continue
        source_url = item["source_url"].strip()
        try:
            parsed = urlsplit(source_url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        safe_query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if not any(
                    marker in key.casefold()
                    for marker in _SENSITIVE_EVIDENCE_URL_KEY_PARTS
                )
            ],
            doseq=True,
        )
        item["source_url"] = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, safe_query, parsed.fragment)
        )
    return sanitized


def _reconcile_observed_stay_budget(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Make dated observed stay prices the single displayed budget truth."""

    reconciled = deepcopy(raw_plan)
    stays = reconciled.get("stay_recommendations")
    budget = reconciled.get("budget")
    if not isinstance(stays, list):
        return reconciled
    observed_totals: list[float] = []
    for stay in stays:
        if not isinstance(stay, dict):
            continue
        price = stay.get("observed_price_per_night_cny")
        status = str(stay.get("price_status") or "")
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or price <= 0
            or status not in {"live_observed", "snapshot_observed"}
        ):
            continue
        stay["planning_estimate_per_night_cny"] = None
        nights = stay.get("nights")
        night_count = int(nights) if isinstance(nights, int) and nights > 0 else 1
        observed_totals.append(float(price) * night_count)
    if not observed_totals or not isinstance(budget, dict):
        return reconciled
    items = budget.get("items")
    if not isinstance(items, list):
        return reconciled
    lodging_items = [
        item
        for item in items
        if isinstance(item, dict)
        and any(marker in str(item.get("name") or "") for marker in ("住宿", "酒店", "民宿", "旅馆"))
    ]
    if len(lodging_items) != len(observed_totals):
        return reconciled
    for item, observed in zip(lodging_items, observed_totals, strict=True):
        item["expected"] = observed
        lower = item.get("lower")
        upper = item.get("upper")
        if isinstance(lower, (int, float)) and not isinstance(lower, bool):
            item["lower"] = min(float(lower), observed)
        if isinstance(upper, (int, float)) and not isinstance(upper, bool):
            item["upper"] = max(float(upper), observed)
    expected_values = [
        item.get("expected")
        for item in items
        if isinstance(item, dict)
    ]
    if expected_values and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in expected_values
    ):
        budget["expected"] = float(sum(expected_values))
    return reconciled


def _coerce_plan_route_numeric_strings(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Convert allowlisted numeric route strings before strict domain validation."""

    normalized = deepcopy(raw_plan)
    days = normalized.get("days")
    if not isinstance(days, list):
        return normalized
    for day in days:
        segments = day.get("route_segments") if isinstance(day, dict) else None
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            distance = segment.get("distance")
            source = str(segment.get("source") or "").casefold()
            if (
                isinstance(distance, (int, float))
                and not isinstance(distance, bool)
                and distance > 20_000
                and any(marker in source for marker in ("amap", "高德"))
            ):
                # AMap route responses use metres while TravelPlanV1 exposes
                # route distance in kilometres. Repair the unambiguous raw-unit
                # carry-over before strict validation.
                segment["distance"] = float(distance) / 1000
            value = segment.get("walking_distance")
            if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value):
                segment["walking_distance"] = float(value)
    return normalized


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
        "Persist one to three meaningfully different optimizer candidates. Pause for a user "
        "decision only when more than one remains; auto-select a single converged plan."
    )
    parameters = {
        "type": "object",
        "properties": {
            "recommended_candidate_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
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
            candidates = _candidate_summaries(
                raw_candidates,
                evidence_coverage=self.service.source_ledger.snapshot(
                    context.session_id
                ).evidence_coverage,
            )
            review = self.service.save_candidate_review(
                context.actor,
                session_id=context.session_id,
                turn_id=context.turn_id,
                candidates=candidates,
                recommended_candidate_id=recommended,
            )
            automatic = len(candidates) == 1
            if automatic:
                review = self.service.select_candidate(
                    context.actor,
                    context.session_id,
                    candidates[0]["candidate_id"],
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
        detail_data["candidates"] = [
            {
                key: value
                for key, value in candidate.items()
                if key not in {"itinerary", "budget_items"}
            }
            for candidate in detail_data["candidates"]
        ]
        if context.runtime_events is not None:
            context.runtime_events.emit(
                (
                    "travel.candidate_review_auto_selected"
                    if automatic
                    else "travel.candidate_review_required"
                ),
                tool_call_id=context.tool_call_id,
                tool_call_record_id=context.tool_call_record_id,
                parent_event_id=context.tool_started_event_id,
                display={
                    "title": "行程方向已确定" if automatic else "候选行程已准备好",
                    "detail": (
                        "时间充足且没有真实取舍，正在直接生成完整计划"
                        if automatic
                        else "选择一个方案后生成完整旅行计划"
                    ),
                },
                ui_metadata={"detail_type": "travel_candidates", "detail_data": detail_data},
                metadata={"candidate_count": len(candidates)},
            )
        return ToolResult(
            output=json.dumps(
                {
                    "status": "selected" if automatic else "waiting_for_user",
                    "code": (
                        "TRAVEL_CANDIDATE_AUTO_SELECTED"
                        if automatic
                        else "TRAVEL_CANDIDATE_REVIEW_REQUIRED"
                    ),
                    "selected_candidate_id": review.selected_candidate_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={
                "code": (
                    "TRAVEL_CANDIDATE_AUTO_SELECTED"
                    if automatic
                    else "TRAVEL_CANDIDATE_REVIEW_REQUIRED"
                ),
                "tool_name": self.name,
            },
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


def recover_intake_draft(metadata: dict[str, Any], messages: list[Any]) -> dict[str, Any]:
    """Replay legacy intake patches once so empty placeholders cannot keep old sessions erased."""

    current = _complete_intake_draft(metadata.get("travel_draft"))
    if metadata.get("travel_draft_version") == TRAVEL_INTAKE_DRAFT_VERSION:
        return current
    replayed = _complete_intake_draft(None)
    replayed_any = False
    for message in messages:
        for call in getattr(message, "tool_calls", []) or []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or function.get("name") != "update_travel_draft":
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    continue
            if not isinstance(arguments, dict):
                continue
            patch = arguments.get("patch")
            clear_fields = arguments.get("clear_fields", [])
            if not isinstance(patch, dict) or set(patch) - set(_INTAKE_FIELDS):
                continue
            if not isinstance(clear_fields, list) or any(
                field not in _INTAKE_FIELDS for field in clear_fields
            ):
                continue
            if set(patch) & set(clear_fields):
                continue
            for field in clear_fields:
                replayed[field] = _empty_intake_value(field)
            for field, value in patch.items():
                if value != _empty_intake_value(field):
                    replayed[field] = value
            replayed_any = True
    if not replayed_any:
        return current
    for field in _INTAKE_FIELDS:
        value = current[field]
        if value != _empty_intake_value(field):
            replayed[field] = value
    return replayed


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


def _intake_turn_user_text(messages: list[Any], turn_id: str) -> str:
    return "\n".join(
        str(getattr(message, "content", "") or "").strip()
        for message in messages
        if getattr(message, "role", "") == "user"
        and str(getattr(message, "turn_id", "") or "") == str(turn_id or "")
        and str(getattr(message, "content", "") or "").strip()
    )


def _ground_intake_patch(patch: dict[str, Any], user_text: str) -> dict[str, Any]:
    """Drop schema placeholders and ungrounded required values from an LLM patch."""

    grounded = deepcopy(patch)
    for field in (
        "destinations",
        "transport_preferences",
        "stay_preferences",
        "interest_tags",
        "hard_constraints",
    ):
        value = grounded.get(field)
        if isinstance(value, list):
            grounded[field] = [
                item
                for item in value
                if str(item or "").strip().casefold() not in _INTAKE_PLACEHOLDERS
            ]
    for field in ("origin", "traveller_type"):
        value = str(grounded.get(field) or "").strip()
        if value.casefold() in _INTAKE_PLACEHOLDERS:
            grounded[field] = ""

    text = str(user_text or "").strip()
    if not text:
        return grounded

    origin = str(grounded.get("origin") or "").strip()
    if origin and not _explicit_origin_in_text(origin, text):
        grounded.pop("origin", None)

    destinations = grounded.get("destinations")
    if isinstance(destinations, list):
        explicit_destinations = [
            item for item in destinations if _place_mentioned(str(item), text)
        ]
        if explicit_destinations:
            grounded["destinations"] = explicit_destinations
        else:
            grounded.pop("destinations", None)

    if not _date_anchor_in_text(text):
        grounded.pop("start_date", None)
        grounded.pop("end_date", None)
    if not re.search(
        r"(?:\d{1,2}|[一二两三四五六七八九十]+)\s*(?:个)?(?:人|位)|"
        r"一个人|独自|单人|自己去|自己玩",
        text,
        flags=re.IGNORECASE,
    ):
        grounded.pop("traveller_count", None)
    if not re.search(
        r"(?:预算|总价|费用|花费|人均)[^。；，,]{0,12}\d|"
        r"\d+(?:\.\d+)?\s*(?:元|块|人民币|rmb|¥)",
        text,
        flags=re.IGNORECASE,
    ):
        grounded.pop("budget_total_cny", None)
    if "budget_level" in grounded and not re.search(
        r"经济|实惠|省钱|穷游|舒适|均衡|品质|轻松|高端",
        text,
    ):
        grounded.pop("budget_level", None)
    return grounded


def _place_mentioned(place: str, text: str) -> bool:
    value = str(place or "").strip()
    if not value:
        return False
    variants = {value, re.sub(r"(?:省|市|自治区|特别行政区)$", "", value)}
    return any(item and item in text for item in variants)


def _explicit_origin_in_text(origin: str, text: str) -> bool:
    variants = {
        re.escape(origin),
        re.escape(re.sub(r"(?:省|市|自治区|特别行政区)$", "", origin)),
    }
    place = "(?:" + "|".join(sorted(item for item in variants if item)) + ")"
    return bool(
        re.search(
            rf"(?:从\s*{place}|{place}\s*(?:出发|启程)|"
            rf"出发地\s*(?:是|在|为)?\s*{place}|我(?:现在)?在\s*{place})",
            text,
        )
    )


def _date_anchor_in_text(text: str) -> bool:
    return bool(
        re.search(
            r"\d{4}[年./-]\d{1,2}(?:[月./-]\d{1,2})?|"
            r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]?|"
            r"(?:今天|明天|后天|大后天|本周|这周|下周|周末|月底|月初|"
            r"国庆|春节|清明|五一|端午|中秋|暑假|寒假)",
            text,
        )
    )


def _intake_missing_fields(draft: dict[str, Any]) -> list[str]:
    checks = (
        ("origin", "出发地"),
        ("destinations", "目的地"),
        ("start_date", "开始日期"),
        ("end_date", "结束日期"),
        ("traveller_count", "人数"),
        ("budget_level", "旅行基调"),
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


def _candidate_summaries(
    value: object,
    *,
    evidence_coverage: float | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise ValueError("Provide between one and three feasible candidates.")
    allowed = {
        "candidate_id", "recommended", "score", "days", "budget", "route_minutes",
        "route_distance_km", "daily_intensity_scores", "evidence_coverage", "warnings",
        "itinerary", "budget_items", "strategy_label", "core_tradeoff",
        "unique_highlights", "omitted_highlights",
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
        candidate = {
            "candidate_id": candidate_id,
            "recommended": bool(raw.get("recommended")),
            "score": float(raw.get("score") or 0),
            "days": safe_days,
            "budget": {key: float(budget.get(key) or 0) for key in ("lower", "expected", "upper")},
            "route_minutes": float(raw.get("route_minutes") or 0),
            "route_distance_km": float(raw.get("route_distance_km") or 0),
            "daily_intensity_scores": [float(item) for item in (raw.get("daily_intensity_scores") or [])[:60]],
            "evidence_coverage": (
                round(min(1.0, max(0.0, evidence_coverage)), 3)
                if evidence_coverage is not None
                else float(raw.get("evidence_coverage") or 0)
            ),
            "warnings": [str(item)[:80] for item in (raw.get("warnings") or [])[:10]],
            "strategy_label": str(raw.get("strategy_label") or "")[:80],
            "core_tradeoff": str(raw.get("core_tradeoff") or "")[:300],
            "unique_highlights": [
                str(item)[:100] for item in (raw.get("unique_highlights") or [])[:6]
            ],
            "omitted_highlights": [
                str(item)[:100] for item in (raw.get("omitted_highlights") or [])[:6]
            ],
        }
        if raw.get("itinerary") is not None:
            candidate["itinerary"] = _candidate_itinerary(
                raw.get("itinerary"), len(safe_days)
            )
        if raw.get("budget_items") is not None:
            candidate["budget_items"] = _candidate_budget_items(raw.get("budget_items"))
        normalized.append(candidate)
    return normalized


def _candidate_itinerary(value: object, expected_days: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"days"}:
        raise ValueError("Candidate itinerary is invalid.")
    days = value.get("days")
    if not isinstance(days, list) or len(days) != expected_days:
        raise ValueError("Candidate itinerary days are invalid.")
    safe_days = []
    for day in days:
        if not isinstance(day, dict):
            raise ValueError("Candidate itinerary day is invalid.")
        activities = day.get("activities")
        segments = day.get("route_segments", [])
        if not isinstance(activities, list) or not 1 <= len(activities) <= 24:
            raise ValueError("Candidate itinerary activities are invalid.")
        if not isinstance(segments, list) or len(segments) > 32:
            raise ValueError("Candidate itinerary routes are invalid.")
        safe_activities = []
        for activity in activities:
            if not isinstance(activity, dict):
                raise ValueError("Candidate itinerary activity is invalid.")
            safe_activities.append(
                {
                    "start": str(activity.get("start") or "")[:5],
                    "end": str(activity.get("end") or "")[:5],
                    "place": str(activity.get("place") or "")[:200],
                }
            )
        safe_segments = []
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("Candidate itinerary route is invalid.")
            safe_segments.append(
                {
                    "from": str(segment.get("from") or "")[:200],
                    "to": str(segment.get("to") or "")[:200],
                    "duration": float(segment.get("duration") or 0),
                    "distance": float(segment.get("distance") or 0),
                    "mode": str(segment.get("mode") or "")[:50],
                }
            )
        safe_days.append(
            {
                "date": str(day.get("date") or "")[:10],
                "city_or_area": str(day.get("city_or_area") or "")[:120],
                "activities": safe_activities,
                "route_segments": safe_segments,
                "daily_budget": float(day.get("daily_budget") or 0),
            }
        )
    return {"days": safe_days}


def _candidate_budget_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("Candidate budget items are invalid.")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Candidate budget item is invalid.")
        result.append(
            {
                "name": str(item.get("name") or "")[:100],
                "lower": float(item.get("lower") or 0),
                "expected": float(item.get("expected") or 0),
                "upper": float(item.get("upper") or 0),
            }
        )
    return result

def _repair_required_error(
    exc: TravelApplicationError,
    raw_plan: dict[str, Any],
    attempt: dict[str, Any],
) -> ToolResult:
    issues = _plan_schema_issues(raw_plan)
    domain_issue = {
        "field": exc.field or "plan",
        "message": exc.message,
    }
    if domain_issue not in issues:
        issues.append(domain_issue)
    return _repair_required_payload(
        code=exc.code,
        message=exc.message,
        issues=issues,
        attempt=attempt,
        field=exc.field,
    )


def _repair_required_payload(
    *,
    code: str,
    message: str,
    issues: list[dict[str, str]],
    attempt: dict[str, Any],
    field: str = "",
) -> ToolResult:
    revision = str(attempt.get("draft_revision") or "")
    payload = {
        "status": "repair_required",
        "code": code,
        "draft_revision": revision,
        "issues": issues[:80],
        "repair_mode": "json_pointer",
        "message": (
            "Submit draft_revision and one repairs array containing all known corrections; "
            "do not resend the complete plan."
        ),
    }
    metadata: dict[str, Any] = {
        "code": code,
        "tool_name": FinalizeTravelPlanTool.name,
        "draft_revision": revision,
        "issues": issues[:80],
    }
    if field:
        metadata["field"] = field
    elif message:
        metadata["message"] = message
    return ToolResult(
        output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        is_error=True,
        metadata=metadata,
    )


def _research_error_with_draft(
    result: ToolResult,
    attempt: dict[str, Any],
) -> ToolResult:
    revision = str(attempt.get("draft_revision") or "")
    metadata = dict(result.metadata)
    metadata["draft_revision"] = revision
    payload = {
        "status": "research_required",
        "code": str(metadata.get("code") or "TRAVEL_RESEARCH_INCOMPLETE"),
        "draft_revision": revision,
        "message": result.output,
        "next": (
            "Complete only the requested research lane, then call finalize_travel_plan with "
            "this draft_revision and repairs (an empty array is allowed). Do not resend plan."
        ),
    }
    return ToolResult(
        output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        is_error=True,
        metadata=metadata,
    )


def _plan_schema_issues(raw_plan: dict[str, Any]) -> list[dict[str, str]]:
    schema = FinalizeTravelPlanTool.parameters["properties"]["plan"]
    issues: list[dict[str, str]] = []
    for error in sorted(
        Draft202012Validator(schema).iter_errors(raw_plan),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    ):
        path = list(error.absolute_path)
        if error.validator == "required" and isinstance(error.validator_value, list):
            missing = [key for key in error.validator_value if key not in error.instance]
            for key in missing:
                issue = {
                    "field": _plan_issue_path([*path, str(key)]),
                    "message": f"'{key}' is a required property",
                }
                if issue not in issues:
                    issues.append(issue)
                if len(issues) >= 79:
                    return issues
            continue
        issue = {"field": _plan_issue_path(path), "message": error.message}
        if issue not in issues:
            issues.append(issue)
        if len(issues) >= 79:
            break
    return issues


def _plan_issue_path(parts: list[Any]) -> str:
    rendered = "plan"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def _draft_error(
    code: str,
    message: str,
    *,
    draft_revision: str = "",
    field: str = "",
) -> ToolResult:
    payload = {
        "status": "error",
        "code": code,
        "message": message,
    }
    metadata: dict[str, Any] = {"code": code, "tool_name": FinalizeTravelPlanTool.name}
    if draft_revision:
        payload["draft_revision"] = draft_revision
        metadata["draft_revision"] = draft_revision
    if field:
        payload["field"] = field
        metadata["field"] = field
    return ToolResult(
        output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        is_error=True,
        metadata=metadata,
    )


def _application_tool_error(exc: TravelApplicationError) -> ToolResult:
    metadata: dict[str, Any] = {
        "code": exc.code,
        "tool_name": FinalizeTravelPlanTool.name,
    }
    if exc.field:
        metadata["field"] = exc.field
    return ToolResult(
        output=exc.message,
        is_error=True,
        metadata=metadata,
    )


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
        missing = ", ".join(snapshot.missing_attempts)
        return _error(
            "TRAVEL_RESEARCH_INCOMPLETE",
            "Configured travel source categories have not all been queried. "
            f"Missing categories: {missing}. Discover or call only those source categories before finalizing.",
        )
    if snapshot.retry_required:
        retry = ", ".join(snapshot.retry_required)
        return _error(
            "TRAVEL_RESEARCH_INCOMPLETE",
            "A travel search source returned no usable results and still requires one narrower "
            f"retry before finalizing. Retry categories: {retry}.",
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
    required_rail_options = min(snapshot.transport_ticket_success_count, 2)
    if required_rail_options and _plan_railway_provenance_count(raw_plan) < required_rail_options:
        status_hint = (
            " Preserve the 12306 not-on-sale date and sale-open date as evidence-backed "
            "estimated rail options."
            if snapshot.transport_ticket_not_on_sale
            else " Preserve the returned service, times, seat, and prices."
        )
        return _error(
            "TRAVEL_RAIL_EVIDENCE_MISSING",
            "12306 ticket queries completed, but the final plan discarded one or both "
            "outbound/return railway results." + status_hint,
        )
    if _plan_requires_concrete_stay(raw_plan):
        return _error(
            "TRAVEL_STAY_REQUIRED",
            "This overnight plan has no concrete stay recommendation. Search one specific hotel "
            "with a matching name, address, coordinates, and identity evidence; keep observed "
            "price evidence separate from a planning estimate.",
        )
    if "lodging" in snapshot.successful and not _plan_has_observed_stay_prices(raw_plan):
        return _error(
            "TRAVEL_HOTEL_PRICE_EVIDENCE_MISSING",
            "The dated hotel search returned usable prices, but the final stay cards did not "
            "preserve them. Reuse the observed nightly price and its Ctrip live-query evidence "
            "for every recommended stay; do not replace it with a planning estimate.",
        )
    if (
        snapshot.forecast_expected
        and _plan_within_forecast_window(raw_plan)
        and not snapshot.forecast_attempted
    ):
        return _error(
            "TRAVEL_WEATHER_FORECAST_REQUIRED",
            "The travel dates are inside the available forecast window, but only historical "
            "weather was used. Call the configured forecast Tool once before finalizing; use "
            "historical climate only as a separate reference.",
        )
    if (
        snapshot.forecast_successful
        and _plan_within_forecast_window(raw_plan)
        and not _plan_has_live_weather(raw_plan)
    ):
        return _error(
            "TRAVEL_WEATHER_FORECAST_EVIDENCE_MISSING",
            "The forecast Tool succeeded for dates inside its window, but weather_summary was "
            "not preserved as live forecast evidence. Reuse the forecast result instead of "
            "replacing it with historical climate.",
        )
    if "weather" in snapshot.successful and not _plan_has_weather_provenance(raw_plan):
        return _error(
            "TRAVEL_WEATHER_EVIDENCE_MISSING",
            "Weather data was queried successfully, but weather_summary does not preserve its "
            "provider and freshness. Reuse the existing weather ToolResult and evidence without "
            "querying it again.",
        )
    if "web" in snapshot.successful and not _plan_has_search_evidence(raw_plan, "web"):
        return _error(
            "TRAVEL_WEB_EVIDENCE_MISSING",
            "Tavily returned usable results, but the final plan discarded them. Preserve one "
            "to three filtered web results as web_article evidence with title, URL, and a short "
            "excerpt; do not claim that web research was unavailable.",
        )
    if "social" in snapshot.successful and not _plan_has_search_evidence(raw_plan, "social"):
        return _error(
            "TRAVEL_SOCIAL_EVIDENCE_MISSING",
            "Xiaohongshu returned usable notes, but the final plan discarded them. Preserve one "
            "to three note titles and short filtered summaries as social_post evidence, using "
            "the returned note id and token in the source URL; do not claim that no notes were found.",
        )
    if snapshot.verified_transit_available and not _plan_has_verified_transit(raw_plan):
        return _error(
            "TRAVEL_TRANSIT_EVIDENCE_MISSING",
            "AMap returned public-transit line details, but the final plan did not preserve any line, boarding stop, and alighting stop. Normalize the verified route instead of replacing it with a planning estimate.",
        )
    if _plan_has_unverified_transit_segments(raw_plan):
        return _error(
            "TRAVEL_ROUTE_EVIDENCE_MISSING",
            "One or more local public-transit segments of at least 2 km are still planning "
            "estimates. Query and preserve AMap route results with line, boarding stop, "
            "alighting stop, duration, and distance before finalizing.",
        )
    return None


def _merge_previous_verified_transit(
    current_plan: dict[str, Any],
    previous_plan: dict[str, Any],
) -> dict[str, Any]:
    """Preserve verified route fields while the model patches another finalizer error."""

    merged = deepcopy(current_plan)
    current_days = merged.get("days")
    previous_days = previous_plan.get("days")
    if not isinstance(current_days, list) or not isinstance(previous_days, list):
        return merged
    previous_by_date = {
        str(day.get("date") or ""): day
        for day in previous_days
        if isinstance(day, dict) and str(day.get("date") or "")
    }
    for current_day in current_days:
        if not isinstance(current_day, dict):
            continue
        previous_day = previous_by_date.get(str(current_day.get("date") or ""))
        current_segments = current_day.get("route_segments")
        previous_segments = (
            previous_day.get("route_segments") if isinstance(previous_day, dict) else None
        )
        if not isinstance(current_segments, list) or not isinstance(previous_segments, list):
            continue
        previous_by_pair = {
            _route_pair(segment): segment
            for segment in previous_segments
            if isinstance(segment, dict) and _verified_transit_segment(segment)
        }
        for index, current_segment in enumerate(current_segments):
            if not isinstance(current_segment, dict) or _verified_transit_segment(current_segment):
                continue
            previous_segment = previous_by_pair.get(_route_pair(current_segment))
            if previous_segment is None and index < len(previous_segments):
                indexed = previous_segments[index]
                if isinstance(indexed, dict) and _verified_transit_segment(indexed):
                    previous_segment = indexed
            if previous_segment is None:
                continue
            for key in (
                "mode",
                "duration",
                "distance",
                "source",
                "evidence_ids",
                "path",
                "transit_legs",
                "walking_distance",
                "fare_cny",
            ):
                if key in previous_segment:
                    current_segment[key] = deepcopy(previous_segment[key])
    return merged


def _merge_previous_live_weather(
    current_plan: dict[str, Any],
    previous_plan: dict[str, Any],
) -> dict[str, Any]:
    """Preserve a verified live forecast while another finalizer field is patched."""

    if _plan_has_live_weather(current_plan) or not _plan_has_live_weather(previous_plan):
        return current_plan
    merged = deepcopy(current_plan)
    previous_weather = previous_plan.get("weather_summary")
    merged["weather_summary"] = deepcopy(previous_weather)

    current_evidence = merged.get("evidence")
    previous_evidence = previous_plan.get("evidence")
    if isinstance(current_evidence, list) and isinstance(previous_evidence, list):
        known_ids = {
            str(item.get("evidence_id") or "")
            for item in current_evidence
            if isinstance(item, dict)
        }
        weather_providers = {
            str(item.get("provider") or "").strip().casefold()
            for item in previous_weather
            if isinstance(item, dict) and str(item.get("provider") or "").strip()
        }
        for item in previous_evidence:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id") or "")
            identity = " ".join(
                str(item.get(key) or "").casefold()
                for key in ("provider", "title", "excerpt")
            )
            is_weather = (
                str(item.get("freshness") or "").casefold() == "live"
                and (
                    str(item.get("provider") or "").strip().casefold()
                    in weather_providers
                    or any(marker in identity for marker in ("weather", "forecast", "天气", "预报"))
                )
            )
            if is_weather and evidence_id not in known_ids:
                current_evidence.append(deepcopy(item))
                known_ids.add(evidence_id)

    previous_days = previous_plan.get("days")
    current_days = merged.get("days")
    if isinstance(previous_days, list) and isinstance(current_days, list):
        adjustments = {
            str(day.get("date") or ""): day.get("weather_adjustment")
            for day in previous_days
            if isinstance(day, dict) and day.get("weather_adjustment")
        }
        for day in current_days:
            if isinstance(day, dict) and str(day.get("date") or "") in adjustments:
                day["weather_adjustment"] = deepcopy(
                    adjustments[str(day.get("date") or "")]
                )
    return merged


def _route_pair(segment: dict[str, Any]) -> tuple[str, str]:
    return (
        " ".join(str(segment.get("from") or "").casefold().split()),
        " ".join(str(segment.get("to") or "").casefold().split()),
    )


def _verified_transit_segment(segment: dict[str, Any]) -> bool:
    source = str(segment.get("source") or "").casefold()
    return bool(segment.get("transit_legs")) and ("amap" in source or "高德" in source)


def _plan_has_observed_stay_prices(raw_plan: object) -> bool:
    if not isinstance(raw_plan, dict):
        return False
    stays = raw_plan.get("stay_recommendations")
    evidence = raw_plan.get("evidence")
    if not isinstance(stays, list) or not stays or not isinstance(evidence, list):
        return False
    evidence_by_id = {
        str(item.get("evidence_id") or "").strip(): item
        for item in evidence
        if isinstance(item, dict) and str(item.get("evidence_id") or "").strip()
    }
    for stay in stays:
        if not isinstance(stay, dict):
            return False
        if stay.get("price_status") not in {"live_observed", "snapshot_observed"}:
            return False
        price = stay.get("observed_price_per_night_cny")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
            return False
        references = stay.get("price_source_evidence_ids")
        if not isinstance(references, list) or not references:
            return False
        if not all(
            _is_observed_lodging_price_evidence(evidence_by_id.get(str(item_id or "")))
            for item_id in references
        ):
            return False
    return True


def _is_observed_lodging_price_evidence(item: object) -> bool:
    if not isinstance(item, dict) or item.get("source_type") != "live_query":
        return False
    identity = " ".join(
        str(item.get(key) or "").casefold()
        for key in ("provider", "title", "source_url")
    )
    return any(marker in identity for marker in ("ctrip", "trip.com", "携程", "hotel-browser"))


def _plan_requires_concrete_stay(raw_plan: object) -> bool:
    if not isinstance(raw_plan, dict):
        return False
    stays = raw_plan.get("stay_recommendations")
    if isinstance(stays, list) and stays:
        return False
    request = raw_plan.get("request")
    if not isinstance(request, dict):
        return False
    try:
        duration_days = int(request.get("duration_days") or 0)
    except (TypeError, ValueError):
        return False
    if duration_days <= 1:
        return False
    preferences = request.get("stay_preferences")
    constraints = request.get("hard_constraints")
    exemption_text = " ".join(
        str(item)
        for values in (preferences, constraints)
        if isinstance(values, list)
        for item in values
    ).casefold()
    exemptions = (
        "无需住宿",
        "不住酒店",
        "住亲友",
        "亲友家",
        "自有住宿",
        "露营",
        "overnight train",
        "no lodging",
        "no hotel",
    )
    return not any(marker in exemption_text for marker in exemptions)


def _plan_has_weather_provenance(raw_plan: object) -> bool:
    items = raw_plan.get("weather_summary") if isinstance(raw_plan, dict) else None
    if not isinstance(items, list) or not items:
        return False
    return all(
        isinstance(item, dict)
        and str(item.get("provider") or "").strip()
        and str(item.get("freshness") or "").strip().casefold() not in {"", "unknown"}
        for item in items
    )


def _plan_has_live_weather(raw_plan: object) -> bool:
    items = raw_plan.get("weather_summary") if isinstance(raw_plan, dict) else None
    return isinstance(items, list) and bool(items) and all(
        isinstance(item, dict)
        and str(item.get("provider") or "").strip()
        and str(item.get("freshness") or "").strip().casefold() == "live"
        for item in items
    )


def _plan_within_forecast_window(raw_plan: object) -> bool:
    request = raw_plan.get("request") if isinstance(raw_plan, dict) else None
    if not isinstance(request, dict):
        return False
    try:
        start = date.fromisoformat(str(request.get("start_date") or ""))
        end = date.fromisoformat(str(request.get("end_date") or ""))
    except ValueError:
        return False
    today = _travel_today()
    return today <= start <= end <= today + timedelta(days=15)


def _travel_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _plan_has_search_evidence(raw_plan: object, category: str) -> bool:
    evidence = raw_plan.get("evidence") if isinstance(raw_plan, dict) else None
    if not isinstance(evidence, list):
        return False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "").casefold()
        provider = str(item.get("provider") or "").casefold()
        if category == "web" and (source_type == "web_article" or "tavily" in provider):
            return True
        if category == "social" and (
            source_type == "social_post"
            or "xiaohongshu" in provider
            or "小红书" in provider
        ):
            return True
    return False


def _merge_ledger_search_evidence(
    raw_plan: dict[str, Any],
    *,
    web: list[dict[str, Any]],
    social: list[dict[str, Any]],
) -> dict[str, Any]:
    """Preserve safe source citations when the model omits child search evidence."""

    merged = deepcopy(raw_plan)
    evidence = merged.get("evidence")
    if not isinstance(evidence, list):
        return merged
    destinations = {
        str(item).casefold()
        for item in (
            merged.get("request", {}).get("destinations", [])
            if isinstance(merged.get("request"), dict)
            else []
        )
        if str(item).strip()
    }
    existing_ids = {
        str(item.get("evidence_id") or "")
        for item in evidence
        if isinstance(item, dict)
    }
    for category, candidates in (("web", web), ("social", social)):
        if _plan_has_search_evidence(merged, category):
            continue
        relevant = [
            item
            for item in candidates
            if not destinations
            or any(
                destination in (
                    str(item.get("title") or "")
                    + " "
                    + str(item.get("excerpt") or "")
                ).casefold()
                for destination in destinations
            )
        ]
        selected = relevant or candidates[:1]
        for item in selected[:3]:
            evidence_id = str(item.get("evidence_id") or "")
            if not evidence_id or evidence_id in existing_ids:
                continue
            evidence.append(deepcopy(item))
            existing_ids.add(evidence_id)
    unknowns = merged.get("unknowns")
    if isinstance(unknowns, list):
        merged["unknowns"] = [
            item
            for item in unknowns
            if not _contradicted_search_unknown(str(item), merged)
        ]
    return merged


def _contradicted_search_unknown(value: str, plan: dict[str, Any]) -> bool:
    text = value.casefold()
    if _plan_has_search_evidence(plan, "social") and any(
        marker in text for marker in ("小红书", "社区经验", "社区笔记", "xhs", "social")
    ):
        return True
    return _plan_has_search_evidence(plan, "web") and any(
        marker in text for marker in ("tavily", "网页资料", "网页搜索", "web search")
    )


def _merge_ledger_structured_results(
    raw_plan: dict[str, Any],
    results: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Replace model estimates only where the server observed matching live facts."""

    merged = deepcopy(raw_plan)
    evidence = merged.get("evidence")
    if not isinstance(evidence, list):
        return merged
    _merge_verified_stay(merged, results, evidence)
    _merge_verified_transit(merged, results, evidence)
    _merge_verified_rail_options(merged, results, evidence)
    return merged


def _merge_verified_rail_options(
    plan: dict[str, Any],
    results: dict[str, list[dict[str, Any]]],
    evidence: list[Any],
) -> None:
    """Choose timetable-compatible trains from the full bounded 12306 evidence."""

    options = plan.get("transport_options")
    request = plan.get("request")
    days = plan.get("days")
    rows = [item for item in results.get("rail_options", []) if isinstance(item, dict)]
    if not isinstance(options, list) or not isinstance(request, dict) or not isinstance(days, list) or not rows:
        return
    origin = str(request.get("origin") or "").strip()
    destinations = [str(item).strip() for item in request.get("destinations") or [] if str(item).strip()]
    if not origin or not destinations or not days:
        return
    original_rail_total = _rail_options_total(options)
    start_date = str(request.get("start_date") or "")
    end_date = str(request.get("end_date") or "")
    first_start = str(((days[0].get("activities") or [{}])[0]).get("start") or "00:00")
    last_end = str(((days[-1].get("activities") or [{}])[-1]).get("end") or "23:59")
    party_size = _request_party_size(request)
    directions = [
        (
            origin in str(option.get("from") or "")
            and any(city in str(option.get("to") or "") for city in destinations),
            any(city in str(option.get("from") or "") for city in destinations)
            and origin in str(option.get("to") or ""),
        )
        for option in options
        if isinstance(option, dict)
    ]
    if not any(outbound for outbound, _ in directions) and any(
        origin in str(row.get("from") or "")
        and any(city in str(row.get("to") or "") for city in destinations)
        for row in rows
    ):
        options.append({"mode": "铁路", "from": origin, "to": destinations[0]})
    if not any(returning for _, returning in directions) and any(
        any(city in str(row.get("from") or "") for city in destinations)
        and origin in str(row.get("to") or "")
        for row in rows
    ):
        options.append({"mode": "铁路", "from": destinations[0], "to": origin})
    for option in options:
        if not isinstance(option, dict) or not _looks_like_rail_option(
            str(option.get("mode") or "")
        ):
            continue
        from_name = str(option.get("from") or "")
        to_name = str(option.get("to") or "")
        outbound = origin in from_name and any(city in to_name for city in destinations)
        returning = any(city in from_name for city in destinations) and origin in to_name
        if not outbound and not returning:
            continue
        candidates = [
            row for row in rows
            if (
                outbound
                and origin in str(row.get("from") or "")
                and any(city in str(row.get("to") or "") for city in destinations)
            ) or (
                returning
                and any(city in str(row.get("from") or "") for city in destinations)
                and origin in str(row.get("to") or "")
            )
        ]
        if outbound:
            compatible = [row for row in candidates if str(row.get("arrival_time") or "") <= first_start]
            same_terminal = [
                row
                for row in compatible
                if _same_rail_terminal(to_name, str(row.get("to") or ""), destinations)
            ]
            chosen = max(
                same_terminal or compatible,
                default=None,
                key=lambda row: str(row.get("arrival_time") or ""),
            )
            travel_date = start_date
        else:
            minimum_departure = _clock_plus_minutes(last_end, 60)
            compatible = [row for row in candidates if str(row.get("departure_time") or "") >= minimum_departure]
            same_terminal = [
                row
                for row in compatible
                if _same_rail_terminal(from_name, str(row.get("from") or ""), destinations)
            ]
            chosen = min(
                same_terminal or compatible,
                default=None,
                key=lambda row: str(row.get("departure_time") or ""),
            )
            if chosen is None:
                same_terminal = [
                    row
                    for row in candidates
                    if _same_rail_terminal(
                        from_name, str(row.get("from") or ""), destinations
                    )
                ]
                fallback = max(
                    same_terminal or candidates,
                    default=None,
                    key=lambda row: str(row.get("departure_time") or ""),
                )
                fallback_departure = str(
                    fallback.get("departure_time") or ""
                ) if fallback else ""
                if fallback is not None and _fit_final_day_before_return(
                    days[-1], fallback_departure, buffer_minutes=60
                ):
                    chosen = fallback
            travel_date = end_date
        if chosen is None or not travel_date:
            continue
        departure_time = str(chosen.get("departure_time") or "")
        arrival_time = str(chosen.get("arrival_time") or "")
        price = chosen.get("price_cny_per_person")
        service = str(chosen.get("service_name") or "")
        option.update(
            {
                "name": f"{service} {chosen.get('from')} → {chosen.get('to')}",
                "from": str(chosen.get("from") or from_name),
                "to": str(chosen.get("to") or to_name),
                "service_name": service,
                "departure": f"{travel_date}T{departure_time}:00+08:00",
                "arrival": f"{travel_date}T{arrival_time}:00+08:00",
                "duration_minutes": float(chosen.get("duration_minutes") or 0),
                "seat": str(chosen.get("seat") or "待复核"),
                "price_cny_per_person": price,
                "price_cny_total": float(price) * party_size if isinstance(price, (int, float)) else None,
                "source": "12306 实时余票查询",
                "summary": "已从当日完整返回片段中选择与行程时间兼容的车次。",
            }
        )
        evidence_id = _append_source_evidence(
            evidence,
            prefix="12306-rail",
            provider="铁路 12306",
            title=f"车次：{service} {chosen.get('from')} → {chosen.get('to')}",
            source_url="https://www.12306.cn/",
            facts=[
                f"{departure_time} → {arrival_time}",
                f"{chosen.get('seat')} ¥{price:g}" if isinstance(price, (int, float)) else str(chosen.get("seat") or ""),
            ],
        )
        option["evidence_ids"] = [evidence_id]
    _reconcile_rail_budget(plan, original_rail_total, _rail_options_total(options))


def _fit_final_day_before_return(
    day: object,
    departure_time: str,
    *,
    buffer_minutes: int,
) -> bool:
    """Move final-day activities earlier while preserving order and duration."""

    if not isinstance(day, dict):
        return False
    activities = day.get("activities")
    departure_minutes = _clock_minutes(departure_time)
    if not isinstance(activities, list) or not activities or departure_minutes is None:
        return False
    durations: list[int] = []
    for activity in activities:
        if not isinstance(activity, dict):
            return False
        start = _clock_minutes(str(activity.get("start") or ""))
        end = _clock_minutes(str(activity.get("end") or ""))
        if start is None or end is None or end <= start:
            return False
        durations.append(end - start)
    gap_minutes = 15
    cutoff = departure_minutes - max(buffer_minutes, 0)
    required = sum(durations) + gap_minutes * (len(durations) - 1)
    if cutoff - required < 6 * 60:
        return False
    cursor = cutoff
    for activity, duration in zip(reversed(activities), reversed(durations), strict=True):
        start = cursor - duration
        activity["start"] = _format_clock(start)
        activity["end"] = _format_clock(cursor)
        cursor = start - gap_minutes
    assumptions = day.get("fallback_plan")
    note = "返程车次较早，末日活动已按原时长前移，并预留至少60分钟进站缓冲。"
    if isinstance(assumptions, str):
        if note not in assumptions:
            day["fallback_plan"] = f"{assumptions}；{note}"[:600]
    else:
        day["fallback_plan"] = note
    return True


def _reconcile_persisted_transport_envelope(
    raw_plan: dict[str, Any],
) -> dict[str, Any]:
    """Fit final-day activities to an already evidenced return after restart."""

    plan = deepcopy(raw_plan)
    request = plan.get("request")
    days = plan.get("days")
    options = plan.get("transport_options")
    evidence = plan.get("evidence")
    if not all(
        isinstance(value, expected)
        for value, expected in (
            (request, dict),
            (days, list),
            (options, list),
            (evidence, list),
        )
    ) or not days:
        return plan
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence
        if isinstance(item, dict)
    }
    origin = str(request.get("origin") or "").casefold()
    destinations = tuple(
        str(item).casefold() for item in request.get("destinations") or []
    )
    end_date = str(request.get("end_date") or "")
    departures: list[int] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        from_name = str(option.get("from") or "").casefold()
        to_name = str(option.get("to") or "").casefold()
        if not (
            any(destination in from_name for destination in destinations)
            and origin in to_name
            and _transport_option_has_rail_evidence(option, evidence_by_id)
        ):
            continue
        departure = str(option.get("departure") or "")
        if "T" not in departure or not departure.startswith(end_date):
            continue
        minutes = _clock_minutes(departure.split("T", 1)[1][:5])
        if minutes is not None:
            departures.append(minutes)
    if not departures:
        return plan
    earliest_departure = min(departures)
    last_day = days[-1]
    activities = last_day.get("activities") if isinstance(last_day, dict) else None
    if not isinstance(activities, list) or not activities:
        return plan
    last_end = _clock_minutes(str(activities[-1].get("end") or ""))
    if last_end is None or last_end <= earliest_departure - 60:
        return plan
    _fit_final_day_before_return(
        last_day,
        _format_clock(earliest_departure),
        buffer_minutes=60,
    )
    return plan


def _transport_option_has_rail_evidence(
    option: dict[str, Any],
    evidence_by_id: dict[str, Any],
) -> bool:
    source = " ".join(
        str(option.get(key) or "")
        for key in ("source", "summary", "service_name", "mode")
    ).casefold()
    if any(marker in source for marker in ("12306", "铁路", "高铁", "动车")):
        return True
    references = option.get("evidence_ids")
    if not isinstance(references, list):
        return False
    for evidence_id in references:
        item = evidence_by_id.get(str(evidence_id))
        identity = " ".join(
            str(item.get(key) or "")
            for key in ("provider", "title", "source_url")
        ).casefold() if isinstance(item, dict) else ""
        if any(marker in identity for marker in ("12306", "铁路")):
            return True
    return False


def _clock_minutes(value: str) -> int | None:
    match = re.fullmatch(r"(\d{2}):(\d{2})", str(value or "").strip())
    if not match:
        return None
    hours, minutes = (int(part) for part in match.groups())
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def _format_clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _same_rail_terminal(
    planned: str,
    observed: str,
    destination_cities: list[str],
) -> bool:
    planned_name = re.sub(r"[\s站]", "", str(planned or ""))
    observed_name = re.sub(r"[\s站]", "", str(observed or ""))
    if not planned_name or not observed_name:
        return False
    generic_names = {
        re.sub(r"[\s站]", "", item)
        for item in destination_cities
        if str(item).strip()
    }
    if planned_name in generic_names:
        return False
    return planned_name == observed_name


def _rail_options_total(options: list[Any]) -> float | None:
    totals = [
        float(item["price_cny_total"])
        for item in options
        if isinstance(item, dict)
        and _looks_like_rail_option(str(item.get("mode") or ""))
        and isinstance(item.get("price_cny_total"), int | float)
        and not isinstance(item.get("price_cny_total"), bool)
    ]
    return sum(totals) if totals else None


def _reconcile_rail_budget(
    plan: dict[str, Any],
    original_total: float | None,
    verified_total: float | None,
) -> None:
    if verified_total is None:
        return
    budget = plan.get("budget")
    if not isinstance(budget, dict):
        return
    items = budget.get("items")
    if not isinstance(items, list):
        return
    rail_item = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and any(
                marker in str(item.get("name") or "")
                for marker in ("铁路", "火车", "高铁", "动车", "跨城交通")
            )
        ),
        None,
    )
    if rail_item is None:
        return
    current_expected = rail_item.get("expected")
    baseline = (
        float(current_expected)
        if isinstance(current_expected, int | float) and not isinstance(current_expected, bool)
        else original_total
    )
    if baseline is None:
        return
    expected_delta = verified_total - baseline
    rail_item["expected"] = verified_total
    upper = rail_item.get("upper")
    upper_delta = 0.0
    if isinstance(upper, int | float) and not isinstance(upper, bool):
        normalized_upper = max(float(upper), verified_total)
        upper_delta = normalized_upper - float(upper)
        rail_item["upper"] = normalized_upper
    lower = rail_item.get("lower")
    lower_delta = 0.0
    if isinstance(lower, int | float) and not isinstance(lower, bool):
        normalized_lower = min(float(lower), verified_total)
        lower_delta = normalized_lower - float(lower)
        rail_item["lower"] = normalized_lower
    total_expected = budget.get("expected")
    if isinstance(total_expected, int | float) and not isinstance(total_expected, bool):
        budget["expected"] = max(0.0, float(total_expected) + expected_delta)
    total_lower = budget.get("lower")
    if isinstance(total_lower, int | float) and not isinstance(total_lower, bool):
        budget["lower"] = max(0.0, float(total_lower) + lower_delta)
    total_upper = budget.get("upper")
    if isinstance(total_upper, int | float) and not isinstance(total_upper, bool):
        budget["upper"] = max(0.0, float(total_upper) + upper_delta)
    if isinstance(budget.get("expected"), int | float):
        budget["upper"] = max(
            float(budget.get("upper") or 0),
            float(budget["expected"]),
        )
        budget["lower"] = min(
            float(budget.get("lower") or 0),
            float(budget["expected"]),
        )


def _clock_plus_minutes(value: str, minutes: int) -> str:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (TypeError, ValueError):
        return value
    total = min(23 * 60 + 59, hour * 60 + minute + minutes)
    return f"{total // 60:02d}:{total % 60:02d}"


def _looks_like_rail_option(value: str) -> bool:
    text = value.casefold()
    return any(
        marker in text
        for marker in ("rail", "train", "铁路", "高铁", "动车", "火车", "城际")
    )


def _request_party_size(request: dict[str, Any]) -> int:
    direct = request.get("party_size")
    if isinstance(direct, int) and direct > 0:
        return direct
    travellers = request.get("travellers")
    if isinstance(travellers, list):
        total = sum(
            int(item.get("count") or 0)
            for item in travellers
            if isinstance(item, dict) and isinstance(item.get("count"), int)
        )
        if total > 0:
            return total
    return 1


def _merge_verified_stay(
    plan: dict[str, Any],
    results: dict[str, list[dict[str, Any]]],
    evidence: list[Any],
) -> None:
    stays = plan.get("stay_recommendations")
    if not isinstance(stays, list):
        return
    pois = [item for item in results.get("map_pois", []) if isinstance(item, dict)]
    observations = [
        item for item in results.get("hotel_observations", []) if isinstance(item, dict)
    ]
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for observation in observations:
        for row in observation.get("hotels") or []:
            if isinstance(row, dict):
                candidates.append((observation, row))
    for stay in stays:
        if not isinstance(stay, dict):
            continue
        hotel_name = str(stay.get("hotel_name") or "")
        matched = _best_strict_named_pair(hotel_name, candidates)
        poi = _best_strict_named_row(hotel_name, pois)
        if matched is None and len(stays) == 1:
            fallback = _best_joint_hotel_observation(candidates, pois)
            if fallback is not None:
                matched, poi = fallback
                hotel_name = str(matched[1].get("name") or "")
        if poi is not None and _looks_like_hotel_poi(poi):
            stay["hotel_name"] = hotel_name or str(poi.get("name") or "")
            stay["address"] = str(poi.get("address") or stay.get("address") or "")
            location = _location_object(poi.get("location"))
            if location is not None:
                stay["location"] = location
            identity_id = _append_source_evidence(
                evidence,
                prefix="amap-hotel",
                provider="高德地图",
                title=f"酒店地点：{stay['hotel_name']}",
                source_url="https://ditu.amap.com/",
                facts=[stay["hotel_name"], stay["address"]],
            )
            stay["evidence_ids"] = _append_reference(stay.get("evidence_ids"), identity_id)
            hotel_name = str(stay.get("hotel_name") or hotel_name)
        if matched is None:
            continue
        observation, row = matched
        price = row.get("observed_price_per_night_cny")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            continue
        stay["observed_price_per_night_cny"] = float(price)
        stay["planning_estimate_per_night_cny"] = None
        stay["price_status"] = "live_observed"
        source_url = str(row.get("source_url") or "").strip()
        if not source_url.startswith("https://hotels.ctrip.com/"):
            source_url = "https://hotels.ctrip.com/hotels/list"
        price_id = _append_source_evidence(
            evidence,
            prefix="ctrip-price",
            provider="携程账号只读查询",
            title=f"指定日期房价：{row.get('name') or hotel_name}",
            source_url=source_url,
            facts=[
                f"观察价 ¥{price:g}/晚",
                str(observation.get("query") or {}),
            ],
            retrieved_at=str(observation.get("retrieved_at") or ""),
        )
        stay["price_source_evidence_ids"] = _append_reference(
            stay.get("price_source_evidence_ids"), price_id
        )


def _merge_verified_transit(
    plan: dict[str, Any],
    results: dict[str, list[dict[str, Any]]],
    evidence: list[Any],
) -> None:
    anchors = _plan_and_poi_anchors(plan, results.get("map_pois", []))
    snapshots: list[tuple[str, str, dict[str, Any]]] = []
    for item in results.get("transit_routes", []):
        if not isinstance(item, dict):
            continue
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        route = item.get("route") if isinstance(item.get("route"), dict) else {}
        origin = _anchor_name(arguments.get("origin") or route.get("origin"), anchors)
        destination = _anchor_name(
            arguments.get("destination") or route.get("destination"), anchors
        )
        transits = route.get("transits")
        if origin and destination and isinstance(transits, list) and transits:
            snapshots.append((origin, destination, {"route": route, "transit": transits[0]}))
    for day in plan.get("days") or []:
        segments = day.get("route_segments") if isinstance(day, dict) else None
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict) or _verified_transit_segment(segment):
                continue
            matched = next(
                (
                    payload
                    for origin, destination, payload in snapshots
                    if _same_place(segment.get("from"), origin)
                    and _same_place(segment.get("to"), destination)
                ),
                None,
            )
            if matched is None:
                continue
            transit = matched["transit"]
            legs = _transit_legs_from_snapshot(transit)
            walking = _number_or_none(transit.get("walking_distance"))
            if not legs or (walking is not None and walking > 2_000):
                continue
            route = matched["route"]
            duration_seconds = _number_or_none(transit.get("duration"))
            distance = _number_or_none(transit.get("distance"))
            if distance is None:
                distance = _number_or_none(route.get("distance"))
            distance_km = distance / 1000 if distance is not None else None
            segment.update(
                {
                    "mode": "公交/地铁",
                    "duration": round((duration_seconds or 0) / 60, 1),
                    "distance": distance_km
                    if distance_km is not None
                    else float(segment.get("distance") or 0),
                    "source": "高德地图实时公交路线",
                    "transit_legs": legs,
                    "walking_distance": walking or 0,
                    "fare_cny": _number_or_none(transit.get("cost")),
                }
            )
            route_id = _append_source_evidence(
                evidence,
                prefix="amap-transit",
                provider="高德地图",
                title=f"公交路线：{segment.get('from')} → {segment.get('to')}",
                source_url="https://ditu.amap.com/",
                facts=[
                    f"{leg['line_name']}：{leg['departure_stop']} → {leg['arrival_stop']}"
                    for leg in legs
                ],
            )
            segment["evidence_ids"] = _append_reference(
                segment.get("evidence_ids"), route_id
            )


def _best_named_row(name: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [(_name_score(name, row.get("name")), row) for row in rows]
    scored = [item for item in scored if item[0] > 0]
    return max(scored, default=(0, None), key=lambda item: item[0])[1]


def _best_strict_named_row(
    name: str, rows: list[dict[str, Any]]
) -> dict[str, Any] | None:
    expected = _normalized_name(name)
    if len(expected) < 4:
        return None
    matched = [
        row
        for row in rows
        if len(candidate := _normalized_name(row.get("name"))) >= 4
        and (candidate == expected or candidate in expected or expected in candidate)
    ]
    return max(
        matched,
        default=None,
        key=lambda row: len(_normalized_name(row.get("name"))),
    )


def _normalized_name(value: object) -> str:
    return "".join(
        character for character in str(value or "").casefold() if character.isalnum()
    )


def _best_strict_named_pair(
    name: str,
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    expected = _normalized_name(name)
    if len(expected) < 4:
        return None
    matched = [
        (observation, row)
        for observation, row in rows
        if len(candidate := _normalized_name(row.get("name"))) >= 4
        and (candidate == expected or candidate in expected or expected in candidate)
    ]
    return max(
        matched,
        default=None,
        key=lambda item: len(_normalized_name(item[1].get("name"))),
    )


def _best_joint_hotel_observation(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    pois: list[dict[str, Any]],
) -> tuple[tuple[dict[str, Any], dict[str, Any]], dict[str, Any]] | None:
    verified: list[
        tuple[float, tuple[dict[str, Any], dict[str, Any]], dict[str, Any]]
    ] = []
    for observation, row in rows:
        price = row.get("observed_price_per_night_cny")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            continue
        poi = _best_strict_named_row(str(row.get("name") or ""), pois)
        if poi is None or not _looks_like_hotel_poi(poi):
            continue
        verified.append((float(price), (observation, row), poi))
    if not verified:
        return None
    _, matched, poi = min(verified, key=lambda item: item[0])
    return matched, poi


def _name_score(left: object, right: object) -> int:
    a = "".join(character for character in str(left or "").casefold() if character.isalnum())
    b = "".join(character for character in str(right or "").casefold() if character.isalnum())
    if len(a) < 3 or len(b) < 3:
        return 0
    if a == b:
        return 1000 + len(a)
    if a in b or b in a:
        return min(len(a), len(b))
    common = len(set(a) & set(b))
    return common if common >= 5 else 0


def _looks_like_hotel_poi(row: dict[str, Any]) -> bool:
    typecode = str(row.get("typecode") or "")
    text = " ".join(str(row.get(key) or "") for key in ("name", "type"))
    return typecode.startswith("10") or any(
        marker in text for marker in ("酒店", "宾馆", "旅馆", "客栈", "民宿", "公寓")
    )


def _plan_and_poi_anchors(
    plan: dict[str, Any], pois: list[dict[str, Any]]
) -> dict[tuple[float, float], str]:
    anchors: dict[tuple[float, float], str] = {}
    for poi in pois:
        if isinstance(poi, dict):
            key = _coordinate_key(poi.get("location"))
            if key:
                anchors[key] = str(poi.get("name") or "")
    for stay in plan.get("stay_recommendations") or []:
        if isinstance(stay, dict) and (key := _coordinate_key(stay.get("location"))):
            anchors[key] = str(stay.get("hotel_name") or "")
    for day in plan.get("days") or []:
        for activity in day.get("activities") or [] if isinstance(day, dict) else []:
            if isinstance(activity, dict) and (key := _coordinate_key(activity.get("location"))):
                anchors[key] = str(activity.get("place") or "")
    return anchors


def _coordinate_key(value: object) -> tuple[float, float] | None:
    try:
        if isinstance(value, dict):
            longitude, latitude = value.get("longitude"), value.get("latitude")
        else:
            longitude, latitude = str(value or "").split(",", 1)
        return round(float(longitude), 5), round(float(latitude), 5)
    except (TypeError, ValueError):
        return None


def _location_object(value: object) -> dict[str, float] | None:
    key = _coordinate_key(value)
    return {"longitude": key[0], "latitude": key[1]} if key else None


def _anchor_name(value: object, anchors: dict[tuple[float, float], str]) -> str:
    key = _coordinate_key(value)
    return anchors.get(key, "") if key else ""


def _same_place(left: object, right: object) -> bool:
    return _name_score(left, right) > 0


def _transit_legs_from_snapshot(transit: dict[str, Any]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    for segment in transit.get("segments") or []:
        bus = segment.get("bus") if isinstance(segment, dict) else None
        for line in bus.get("buslines") or [] if isinstance(bus, dict) else []:
            if not isinstance(line, dict):
                continue
            departure = line.get("departure_stop")
            arrival = line.get("arrival_stop")
            line_name = str(line.get("name") or "").strip()
            departure_name = str(departure.get("name") or "") if isinstance(departure, dict) else str(departure or "")
            arrival_name = str(arrival.get("name") or "") if isinstance(arrival, dict) else str(arrival or "")
            if not line_name or not departure_name or not arrival_name:
                continue
            via = line.get("via_stops")
            legs.append(
                {
                    "mode": "公交/地铁",
                    "line_name": line_name,
                    "departure_stop": departure_name,
                    "arrival_stop": arrival_name,
                    "via_stops": [
                        str(item.get("name") or "")
                        for item in via[:8]
                        if isinstance(item, dict) and item.get("name")
                    ] if isinstance(via, list) else [],
                }
            )
    return legs[:12]


def _number_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _append_source_evidence(
    evidence: list[Any],
    *,
    prefix: str,
    provider: str,
    title: str,
    source_url: str,
    facts: list[str],
    retrieved_at: str = "",
) -> str:
    slug = str(abs(hash((prefix, title))))[:12]
    evidence_id = f"{prefix}-{slug}"
    existing = next(
        (
            item
            for item in evidence
            if isinstance(item, dict) and item.get("evidence_id") == evidence_id
        ),
        None,
    )
    if existing is None:
        evidence.append(
            {
                "evidence_id": evidence_id,
                "source_type": "live_query",
                "provider": provider,
                "title": title,
                "source_url": source_url,
                "retrieved_at": retrieved_at or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "freshness": "live",
                "excerpt": "；".join(item for item in facts if item)[:500],
                "facts": [item[:300] for item in facts if item][:10],
                "confidence": 0.9,
            }
        )
    return evidence_id


def _append_reference(value: object, evidence_id: str) -> list[str]:
    references = [str(item) for item in value] if isinstance(value, list) else []
    if evidence_id not in references:
        references.append(evidence_id)
    return references


def _plan_railway_provenance_count(raw_plan: object) -> int:
    if not isinstance(raw_plan, dict):
        return 0
    evidence = raw_plan.get("evidence")
    options = raw_plan.get("transport_options")
    if not isinstance(evidence, list) or not isinstance(options, list):
        return 0
    evidence_by_id = {
        str(item.get("evidence_id") or "").strip(): item
        for item in evidence
        if isinstance(item, dict) and str(item.get("evidence_id") or "").strip()
    }
    count = 0
    for option in options:
        if not isinstance(option, dict):
            continue
        option_text = " ".join(
            str(option.get(key) or "")
            for key in ("name", "mode", "service_name", "source", "summary")
        ).casefold()
        if not any(
            marker in option_text
            for marker in (
                "rail",
                "train",
                "12306",
                "铁路",
                "高铁",
                "动车",
                "火车",
            )
        ):
            continue
        references = option.get("evidence_ids")
        if not isinstance(references, list):
            continue
        if any(
            _is_railway_source_evidence(evidence_by_id.get(str(item_id or "")))
            for item_id in references
        ):
            count += 1
    return count


def _is_railway_source_evidence(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    facts = item.get("facts")
    text = " ".join(
        [
            str(item.get("provider") or ""),
            str(item.get("title") or ""),
            str(item.get("excerpt") or ""),
            *(str(fact) for fact in (facts if isinstance(facts, list) else [])),
        ]
    ).casefold()
    return any(
        marker in text
        for marker in ("12306", "rail", "train", "铁路", "高铁", "动车", "火车")
    )


def _plan_has_unverified_transit_segments(raw_plan: object) -> bool:
    days = raw_plan.get("days") if isinstance(raw_plan, dict) else None
    if not isinstance(days, list):
        return False
    transit_markers = ("公交", "地铁", "bus", "subway", "metro", "transit")
    unresolved_mode_markers = ("规划估算", "planning estimate", "planning_estimate")
    for day in days:
        segments = day.get("route_segments") if isinstance(day, dict) else None
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            mode = str(segment.get("mode") or "").casefold()
            try:
                distance = float(segment.get("distance") or 0)
            except (TypeError, ValueError):
                distance = 0
            if distance < 2 or not any(
                marker in mode for marker in (*transit_markers, *unresolved_mode_markers)
            ):
                continue
            source = str(segment.get("source") or "").casefold()
            if not ("amap" in source or "高德" in source) or not segment.get("transit_legs"):
                return True
    return False


def _plan_has_verified_transit(raw_plan: object) -> bool:
    days = raw_plan.get("days") if isinstance(raw_plan, dict) else None
    if not isinstance(days, list):
        return False
    for day in days:
        segments = day.get("route_segments") if isinstance(day, dict) else None
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict) or not segment.get("transit_legs"):
                continue
            source = str(segment.get("source") or "").casefold()
            if "amap" in source or "高德" in source:
                return True
    return False
