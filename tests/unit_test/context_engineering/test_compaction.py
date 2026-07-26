import json

import pytest

from agent.context.compaction import CompactionStore, LLMContextCompactor, validate_compaction
from agent.core.turns import group_messages_by_turn
from agent.message import Message
from agent.protocols.llm import LLMResponse


class FakeLLM:
    def __init__(self, content, metadata=None):
        self.content = content
        self.metadata = metadata or {}
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(content=self.content, metadata=self.metadata)


def _turns(count=2):
    messages = []
    for index in range(1, count + 1):
        messages.extend(
            [
                Message(role="user", content=f"question {index}", turn_id=f"turn-{index}", turn_index=index),
                Message(role="assistant", content=f"answer {index}", turn_id=f"turn-{index}", turn_index=index),
            ]
        )
    return group_messages_by_turn(messages)


def test_compaction_round_trip_and_source_invalidation(tmp_path):
    payload = {field: [] for field in (
        "topics", "user_questions", "entities", "decisions", "confirmed_facts",
        "unresolved_items", "constraints", "files_and_errors", "tool_result_references",
    )}
    llm = FakeLLM(json.dumps(payload))
    turns = _turns()
    record = LLMContextCompactor(llm, "strict json").compact("session", turns)
    store = CompactionStore(tmp_path / "compactions")
    store.save(record)

    loaded = store.load("session")
    assert loaded == record
    assert validate_compaction(record, turns)
    turns[0].messages[0].content = "changed source"
    assert not validate_compaction(record, turns)


def test_compactor_rejects_invalid_json_without_persisting(tmp_path):
    llm = FakeLLM("not json")
    with pytest.raises(json.JSONDecodeError):
        LLMContextCompactor(llm, "strict json").compact("session", _turns())
    assert CompactionStore(tmp_path / "compactions").load("session") is None


def test_compactor_sends_only_new_turns_with_previous_state():
    payload = {field: [] for field in (
        "topics", "user_questions", "entities", "decisions", "confirmed_facts",
        "unresolved_items", "constraints", "files_and_errors", "tool_result_references",
    )}
    llm = FakeLLM(json.dumps(payload))
    compactor = LLMContextCompactor(llm, "strict json")
    previous = compactor.compact("session", _turns(2))

    current = compactor.compact("session", _turns(3), previous=previous)
    input_payload = json.loads(llm.calls[-1][-1]["content"])

    assert [turn["turn_index"] for turn in input_payload["new_turns"]] == [3]
    assert current.source_start_turn_index == 1
    assert current.source_end_turn_index == 3


def test_compactor_traces_provider_usage_and_configured_cost(caplog):
    payload = {field: [] for field in (
        "topics", "user_questions", "entities", "decisions", "confirmed_facts",
        "unresolved_items", "constraints", "files_and_errors", "tool_result_references",
    )}
    llm = FakeLLM(
        json.dumps(payload),
        metadata={
            "endpoint_name": "compact-fast",
            "model": "fast-model",
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
            "input_price_per_million": 2.0,
            "output_price_per_million": 4.0,
        },
    )
    caplog.set_level("INFO", logger="zcagent.agent.context")

    LLMContextCompactor(
        llm,
        "strict json",
        phase="background",
    ).compact("session", _turns())

    usage = next(record for record in caplog.records if record.event == "context.compaction.usage")
    assert usage.fields == {
        "session_id": "session",
        "phase": "background",
        "endpoint": "compact-fast",
        "model": "fast-model",
        "prompt_count": 1200,
        "completion_count": 300,
        "total_count": 1500,
        "usage_unit": "tokens",
        "usage_available": True,
        "estimated_cost": 0.0036,
        "cost_available": True,
    }
