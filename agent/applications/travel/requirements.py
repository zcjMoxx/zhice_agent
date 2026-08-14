"""Provider-neutral LLM extraction of reviewable travel requirements."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.protocols.llm import (
    LLMGenerationOptions,
    LLMProvider,
    LLMProviderError,
    LLMResponseFormat,
)

from .service import TravelApplicationError

_ALLOWED_FIELDS = {
    "intent",
    "intent_topic",
    "origin",
    "destinations",
    "start_date",
    "end_date",
    "traveller_type",
    "traveller_count",
    "budget_total_cny",
    "budget_level",
    "transport_preferences",
    "stay_preferences",
    "interest_tags",
    "pace",
    "planning_mode",
    "hard_constraints",
}
_INTENT_VALUES = {
    "travel_requirement",
    "assistant_greeting",
    "assistant_identity",
    "assistant_capabilities",
    "planner_help",
    "unrelated",
}
_INTENT_TOPIC_VALUES = {
    "",
    "dates",
    "travellers",
    "budget",
    "preferences",
    "data_sources",
    "models",
    "workflow",
}
_PACE_VALUES = {"", "relaxed", "balanced", "intensive"}
_MODE_VALUES = {"", "quick", "deep"}
_BUDGET_LEVEL_VALUES = {"", "economy", "balanced", "comfortable"}
_TRAVEL_REQUIREMENT_RESPONSE_FORMAT = LLMResponseFormat(
    name="travel_requirement_draft",
    schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {"type": "string", "enum": sorted(_INTENT_VALUES)},
            "intent_topic": {"type": "string", "enum": sorted(_INTENT_TOPIC_VALUES)},
            "origin": {"type": "string", "maxLength": 120},
            "destinations": {
                "type": "array",
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1, "maxLength": 120},
            },
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "traveller_type": {"type": "string", "maxLength": 40},
            "traveller_count": {
                "anyOf": [
                    {"type": "integer", "minimum": 1, "maximum": 50},
                    {"type": "null"},
                ]
            },
            "budget_total_cny": {
                "anyOf": [
                    {"type": "number", "minimum": 100, "maximum": 10_000_000},
                    {"type": "null"},
                ]
            },
            "budget_level": {"type": "string", "enum": sorted(_BUDGET_LEVEL_VALUES)},
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
            "pace": {"type": "string", "enum": sorted(_PACE_VALUES)},
            "planning_mode": {"type": "string", "enum": sorted(_MODE_VALUES)},
            "hard_constraints": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1, "maxLength": 300},
            },
        },
        "required": sorted(_ALLOWED_FIELDS),
    },
)
_TRAVEL_REQUIREMENT_GENERATION_OPTIONS = LLMGenerationOptions(temperature=0.0)


@dataclass(frozen=True)
class TravelRequirementDraft:
    """Strict allowlisted fields shown to the user before planning."""

    intent: str = "travel_requirement"
    intent_topic: str = ""
    origin: str = ""
    destinations: tuple[str, ...] = ()
    start_date: str = ""
    end_date: str = ""
    traveller_type: str = ""
    traveller_count: int | None = None
    budget_total_cny: float | None = None
    budget_level: str = ""
    transport_preferences: tuple[str, ...] = ()
    stay_preferences: tuple[str, ...] = ()
    interest_tags: tuple[str, ...] = ()
    pace: str = ""
    planning_mode: str = ""
    hard_constraints: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: object) -> TravelRequirementDraft:
        if not isinstance(value, dict):
            raise _invalid()
        if set(value) != _ALLOWED_FIELDS:
            raise _invalid()
        start = _optional_date(value["start_date"])
        end = _optional_date(value["end_date"])
        if start and end and end < start:
            raise _invalid()
        count = _optional_integer(value["traveller_count"], minimum=1, maximum=50)
        budget = _optional_number(value["budget_total_cny"], minimum=100, maximum=10_000_000)
        pace = _enum(value["pace"], _PACE_VALUES)
        mode = _enum(value["planning_mode"], _MODE_VALUES)
        budget_level = _enum(value["budget_level"], _BUDGET_LEVEL_VALUES)
        intent = _enum(value["intent"], _INTENT_VALUES)
        intent_topic = _enum(value["intent_topic"], _INTENT_TOPIC_VALUES)
        return cls(
            intent=intent,
            intent_topic=intent_topic,
            origin=_text(value["origin"], 120),
            destinations=_text_list(value["destinations"], maximum=8, item_chars=120),
            start_date=start.isoformat() if start else "",
            end_date=end.isoformat() if end else "",
            traveller_type=_text(value["traveller_type"], 40),
            traveller_count=count,
            budget_total_cny=budget,
            budget_level=budget_level,
            transport_preferences=_text_list(value["transport_preferences"], maximum=12, item_chars=100),
            stay_preferences=_text_list(value["stay_preferences"], maximum=12, item_chars=160),
            interest_tags=_text_list(value["interest_tags"], maximum=20, item_chars=80),
            pace=pace,
            planning_mode=mode,
            hard_constraints=_text_list(value["hard_constraints"], maximum=20, item_chars=300),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "intent_topic": self.intent_topic,
            "origin": self.origin,
            "destinations": list(self.destinations),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "traveller_type": self.traveller_type,
            "traveller_count": self.traveller_count,
            "budget_total_cny": self.budget_total_cny,
            "budget_level": self.budget_level,
            "transport_preferences": list(self.transport_preferences),
            "stay_preferences": list(self.stay_preferences),
            "interest_tags": list(self.interest_tags),
            "pace": self.pace,
            "planning_mode": self.planning_mode,
            "hard_constraints": list(self.hard_constraints),
        }


class TravelRequirementExtractor:
    """Use LLMProvider for semantics, then enforce deterministic output boundaries."""

    def __init__(self, llm: LLMProvider, prompt_loader: PromptLoader):
        self.llm = llm
        self.prompt_loader = prompt_loader

    def extract(self, text: str, *, reference_date: date | None = None) -> TravelRequirementDraft:
        raw_text = str(text or "")
        normalized = " ".join(raw_text.split())
        if not normalized:
            raise TravelApplicationError(
                "TRAVEL_REQUIREMENT_EMPTY", "请先描述旅行需求。", status_code=400
            )
        if len(normalized) > 4000:
            raise TravelApplicationError(
                "TRAVEL_REQUIREMENT_TOO_LONG", "旅行需求不能超过 4000 个字符。", status_code=400
            )
        try:
            prompt = self.prompt_loader.load("travel_requirement_extraction")
        except PromptNotFoundError as exc:
            raise TravelApplicationError(
                "TRAVEL_REQUIREMENT_EXTRACTION_UNAVAILABLE",
                "旅行需求提取暂不可用，请检查运行时 Prompt。",
                status_code=503,
            ) from exc
        user_payload = json.dumps(
            {"reference_date": (reference_date or date.today()).isoformat(), "request": normalized},
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_payload},
        ]
        structured_output = True
        for attempt in range(2):
            try:
                response = _chat_requirement(
                    self.llm,
                    messages,
                    structured_output=structured_output,
                )
            except LLMProviderError as exc:
                if structured_output and _structured_output_unsupported(exc):
                    structured_output = False
                    try:
                        response = _chat_requirement(
                            self.llm,
                            messages,
                            structured_output=False,
                        )
                    except LLMProviderError as fallback_exc:
                        raise _extraction_failed() from fallback_exc
                else:
                    raise _extraction_failed() from exc
            try:
                raw = json.loads(_json_text(response.content))
                draft = TravelRequirementDraft.from_dict(_complete_payload(raw))
                return _complete_fixed_holiday_dates(
                    draft,
                    normalized,
                    reference_date=reference_date or date.today(),
                )
            except (json.JSONDecodeError, TravelApplicationError, TypeError, ValueError) as exc:
                if attempt == 0:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": str(response.content or "")[:4000]},
                        {
                            "role": "user",
                            "content": "上一次输出未通过 JSON 协议校验。请重新判断，只返回协议要求的 JSON 对象。",
                        },
                    ]
                    continue
                raise _invalid() from exc
        raise _invalid()


def _chat_requirement(
    llm: LLMProvider,
    messages: list[dict[str, Any]],
    *,
    structured_output: bool,
):
    if structured_output:
        return llm.chat(
            messages,
            tools=None,
            response_format=_TRAVEL_REQUIREMENT_RESPONSE_FORMAT,
            generation_options=_TRAVEL_REQUIREMENT_GENERATION_OPTIONS,
        )
    return llm.chat(
        messages,
        tools=None,
        generation_options=_TRAVEL_REQUIREMENT_GENERATION_OPTIONS,
    )


def _structured_output_unsupported(exc: LLMProviderError) -> bool:
    """Return true only for request-shape rejection, not auth or transient failures."""

    return exc.http_status == 400 or exc.code in {
        "INVALID_REQUEST",
        "UNSUPPORTED_RESPONSE_FORMAT",
    }


def _extraction_failed() -> TravelApplicationError:
    return TravelApplicationError(
        "TRAVEL_REQUIREMENT_EXTRACTION_FAILED",
        "旅行需求语义提取失败，请稍后重试。",
        status_code=502,
    )


def _json_text(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if len(text) > 20_000:
        raise ValueError("oversized extraction response")
    return text


def _complete_payload(value: object) -> dict[str, Any]:
    """Fill omitted empty fields without accepting unknown or missing intent data."""

    if not isinstance(value, dict) or "intent" not in value:
        raise _invalid()
    if not set(value).issubset(_ALLOWED_FIELDS):
        raise _invalid()
    return {**TravelRequirementDraft().to_dict(), **value}




def _invalid() -> TravelApplicationError:
    return TravelApplicationError(
        "TRAVEL_REQUIREMENT_EXTRACTION_INVALID",
        "模型返回的旅行需求格式无效，请重试。",
        status_code=502,
    )


def _text(value: object, max_chars: int) -> str:
    if not isinstance(value, str) or len(value.strip()) > max_chars:
        raise _invalid()
    return value.strip()


def _text_list(value: object, *, maximum: int, item_chars: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise _invalid()
    items = tuple(_text(item, item_chars) for item in value)
    if any(not item for item in items):
        raise _invalid()
    return items


def _optional_date(value: object) -> date | None:
    if value == "":
        return None
    if not isinstance(value, str):
        raise _invalid()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise _invalid() from exc


def _optional_integer(value: object, *, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _invalid()
    return value


def _optional_number(value: object, *, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid()
    number = float(value)
    if not minimum <= number <= maximum:
        raise _invalid()
    return number


def _enum(value: object, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _invalid()
    return value


def _complete_fixed_holiday_dates(
    draft: TravelRequirementDraft,
    text: str,
    *,
    reference_date: date,
) -> TravelRequirementDraft:
    """Fill only unambiguous fixed-holiday dates omitted by the LLM."""

    if draft.intent != "travel_requirement" or draft.start_date or draft.end_date:
        return draft
    holiday = next(
        (
            (month, day)
            for names, month, day in (
                (("国庆", "国庆节"), 10, 1),
                (("劳动节", "五一"), 5, 1),
                (("元旦",), 1, 1),
            )
            if any(name in text for name in names)
        ),
        None,
    )
    duration_match = re.search(r"(?<!\d)([1-9]|[1-4]\d|50)\s*(?:天|日)", text)
    if holiday is None or duration_match is None:
        return draft
    year_match = re.search(r"(?<!\d)(20\d{2})\s*年", text)
    year = int(year_match.group(1)) if year_match else reference_date.year
    start = date(year, holiday[0], holiday[1])
    if year_match is None and start < reference_date:
        start = date(year + 1, holiday[0], holiday[1])
    end = start + timedelta(days=int(duration_match.group(1)) - 1)
    return TravelRequirementDraft(
        **{**draft.__dict__, "start_date": start.isoformat(), "end_date": end.isoformat()}
    )
