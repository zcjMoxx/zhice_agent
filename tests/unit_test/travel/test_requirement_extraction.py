from __future__ import annotations

import json
from datetime import date

import pytest

from agent.applications.travel.requirements import TravelRequirementExtractor
from agent.applications.travel.service import TravelApplicationError
from agent.prompt_loader import PromptLoader
from agent.protocols.llm import LLMProviderError, LLMResponse


class FakeLLM:
    def __init__(self, result: str | Exception):
        self.result = result
        self.calls = []

    def chat(
        self,
        messages,
        tools=None,
        response_format=None,
        generation_options=None,
    ):
        self.calls.append((messages, tools, response_format, generation_options))
        if isinstance(self.result, Exception):
            raise self.result
        return LLMResponse(content=self.result)


class SequenceLLM(FakeLLM):
    def __init__(self, results):
        super().__init__("")
        self.results = list(results)

    def chat(
        self,
        messages,
        tools=None,
        response_format=None,
        generation_options=None,
    ):
        self.calls.append((messages, tools, response_format, generation_options))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return LLMResponse(content=result)


def _payload():
    return {
        "intent": "travel_requirement",
        "intent_topic": "",
        "origin": "重庆",
        "destinations": ["大理"],
        "start_date": "2026-10-01",
        "end_date": "2026-10-05",
        "traveller_type": "大学生",
        "traveller_count": 2,
        "budget_total_cny": 5000,
        "budget_level": "",
        "transport_preferences": ["铁路"],
        "stay_preferences": [],
        "interest_tags": ["自然风光"],
        "pace": "balanced",
        "planning_mode": "deep",
        "hard_constraints": ["不租车"],
    }


def test_llm_requirement_extraction_returns_strict_review_draft(tmp_path):
    llm = FakeLLM(json.dumps(_payload(), ensure_ascii=False))
    extractor = TravelRequirementExtractor(llm, _prompts(tmp_path))

    draft = extractor.extract(
        "国庆两个大学生从重庆去大理，不租车",
        reference_date=date(2026, 8, 12),
    )

    assert draft.origin == "重庆"
    assert draft.intent == "travel_requirement"
    assert draft.destinations == ("大理",)
    assert draft.traveller_count == 2
    assert draft.hard_constraints == ("不租车",)
    assert llm.calls[0][1] is None
    assert llm.calls[0][2].name == "travel_requirement_draft"
    assert llm.calls[0][3].temperature == 0.0
    assert llm.calls[0][2].schema["additionalProperties"] is False
    assert set(llm.calls[0][2].schema["required"]) == set(_payload())
    user_payload = json.loads(llm.calls[0][0][1]["content"])
    assert user_payload["reference_date"] == "2026-08-12"
    assert user_payload["request"].startswith("国庆")


def test_fixed_national_day_and_duration_fill_dates_when_llm_omits_them(tmp_path):
    payload = {
        **_payload(),
        "start_date": "",
        "end_date": "",
        "traveller_type": "",
        "traveller_count": None,
        "pace": "",
        "planning_mode": "",
    }
    extractor = TravelRequirementExtractor(
        FakeLLM(json.dumps(payload, ensure_ascii=False)),
        _prompts(tmp_path),
    )

    draft = extractor.extract(
        "国庆期间，重庆出发到云南大理游玩5天",
        reference_date=date(2026, 8, 13),
    )

    assert (draft.start_date, draft.end_date) == ("2026-10-01", "2026-10-05")
    assert draft.traveller_count is None
    assert draft.pace == ""
    assert draft.planning_mode == ""


@pytest.mark.parametrize(
    ("intent", "topic", "text"),
    [
        ("assistant_identity", "", "你和主 Agent 是什么关系"),
        ("assistant_greeting", "", "你好"),
        ("assistant_capabilities", "", "可以提供哪些类型的旅行支持"),
        ("planner_help", "models", "规划时底层模型如何参与"),
        ("unrelated", "", "帮我分析一段程序"),
    ],
)
def test_requirement_extractor_returns_bounded_non_planning_intents(
    tmp_path, intent, topic, text
):
    payload = {
        **_payload(),
        "intent": intent,
        "intent_topic": topic,
        "origin": "",
        "destinations": [],
        "start_date": "",
        "end_date": "",
        "traveller_type": "",
        "traveller_count": None,
        "budget_total_cny": None,
        "transport_preferences": [],
        "interest_tags": [],
        "pace": "",
        "planning_mode": "",
        "hard_constraints": [],
    }
    draft = TravelRequirementExtractor(
        FakeLLM(json.dumps(payload, ensure_ascii=False)), _prompts(tmp_path)
    ).extract(text)

    assert draft.intent == intent
    assert draft.intent_topic == topic
    assert draft.origin == ""


def test_identity_question_is_classified_by_llm_under_the_same_strict_schema(tmp_path):
    llm = FakeLLM(json.dumps({"intent": "assistant_identity"}, ensure_ascii=False))

    draft = TravelRequirementExtractor(llm, _prompts(tmp_path)).extract("你是谁")

    assert draft.intent == "assistant_identity"
    assert len(llm.calls) == 1
    assert llm.calls[0][2].strict is True
    assert llm.calls[0][3].temperature == 0.0


def test_meta_shortcut_uses_latest_user_turn_not_an_earlier_question(tmp_path):
    llm = FakeLLM(json.dumps({"intent": "unrelated"}, ensure_ascii=False))
    extractor = TravelRequirementExtractor(llm, _prompts(tmp_path))

    draft = extractor.extract("用户第1轮：你是谁\n助手第2轮：我是旅行助手\n用户第3轮：帮我写代码")

    assert draft.intent == "unrelated"
    assert len(llm.calls) == 1
    assert llm.calls[0][2].name == "travel_requirement_draft"


def test_llm_may_omit_fields_that_are_safely_empty(tmp_path):
    extractor = TravelRequirementExtractor(
        FakeLLM(json.dumps({"intent": "unrelated"}, ensure_ascii=False)),
        _prompts(tmp_path),
    )

    draft = extractor.extract("帮我写一段 Python 代码")

    assert draft.intent == "unrelated"
    assert draft.origin == ""
    assert draft.destinations == ()


def test_invalid_llm_json_is_corrected_once_without_relaxing_schema(tmp_path):
    llm = SequenceLLM([
        "这个问题与旅行无关",
        json.dumps({"intent": "unrelated"}, ensure_ascii=False),
    ])

    draft = TravelRequirementExtractor(llm, _prompts(tmp_path)).extract("帮我写代码")

    assert draft.intent == "unrelated"
    assert len(llm.calls) == 2
    assert "未通过 JSON 协议校验" in llm.calls[1][0][-1]["content"]


def test_schema_unsupported_falls_back_to_prompt_json_and_keeps_validation(tmp_path):
    llm = SequenceLLM([
        LLMProviderError(
            "unsupported response format",
            code="PROVIDER_ERROR",
            http_status=400,
        ),
        json.dumps({"intent": "assistant_identity"}, ensure_ascii=False),
    ])

    draft = TravelRequirementExtractor(llm, _prompts(tmp_path)).extract("你是谁")

    assert draft.intent == "assistant_identity"
    assert llm.calls[0][2] is not None
    assert llm.calls[1][2] is None
    assert llm.calls[0][3].temperature == 0.0
    assert llm.calls[1][3].temperature == 0.0


@pytest.mark.parametrize(
    "error",
    [
        LLMProviderError("auth", code="AUTH_FAILED", http_status=401),
        LLMProviderError("timeout", code="TIMEOUT", retryable=True),
    ],
)
def test_provider_failures_do_not_masquerade_as_schema_incompatibility(tmp_path, error):
    llm = SequenceLLM([error])

    with pytest.raises(TravelApplicationError) as exc_info:
        TravelRequirementExtractor(llm, _prompts(tmp_path)).extract("你是谁")

    assert exc_info.value.code == "TRAVEL_REQUIREMENT_EXTRACTION_FAILED"
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        json.dumps({**_payload(), "unknown": "value"}, ensure_ascii=False),
        json.dumps({**_payload(), "traveller_count": 0}, ensure_ascii=False),
        json.dumps({**_payload(), "pace": "fast"}, ensure_ascii=False),
        json.dumps({**_payload(), "intent": "chat"}, ensure_ascii=False),
        json.dumps({**_payload(), "intent_topic": "secret"}, ensure_ascii=False),
        json.dumps({**_payload(), "end_date": "2026-09-30"}, ensure_ascii=False),
    ],
)
def test_llm_requirement_extraction_rejects_invalid_or_boundary_output(tmp_path, response):
    extractor = TravelRequirementExtractor(SequenceLLM([response, response]), _prompts(tmp_path))

    with pytest.raises(TravelApplicationError) as exc_info:
        extractor.extract("旅行需求")

    assert exc_info.value.code == "TRAVEL_REQUIREMENT_EXTRACTION_INVALID"
    assert "value" not in exc_info.value.message


def test_llm_requirement_extraction_maps_provider_failure_to_safe_error(tmp_path):
    extractor = TravelRequirementExtractor(
        FakeLLM(LLMProviderError("secret upstream body")),
        _prompts(tmp_path),
    )

    with pytest.raises(TravelApplicationError) as exc_info:
        extractor.extract("从重庆去大理")

    assert exc_info.value.code == "TRAVEL_REQUIREMENT_EXTRACTION_FAILED"
    assert "secret" not in exc_info.value.message


def test_llm_requirement_extraction_rejects_empty_and_oversized_input_without_calling_llm(
    tmp_path,
):
    llm = FakeLLM(json.dumps(_payload()))
    extractor = TravelRequirementExtractor(llm, _prompts(tmp_path))

    with pytest.raises(TravelApplicationError) as empty:
        extractor.extract("   ")
    with pytest.raises(TravelApplicationError) as oversized:
        extractor.extract("旅" * 4001)

    assert empty.value.code == "TRAVEL_REQUIREMENT_EMPTY"
    assert oversized.value.code == "TRAVEL_REQUIREMENT_TOO_LONG"
    assert llm.calls == []


def _prompts(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    prompts.joinpath("travel_requirement_extraction.md").write_text(
        "only json",
        encoding="utf-8",
    )
    return PromptLoader(prompts)
