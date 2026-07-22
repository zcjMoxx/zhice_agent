from __future__ import annotations

import json

import pytest

from agent.memory import MemoryStoreError
from agent.memory.context import build_memory_context
from agent.memory.extraction import MemoryExtractionService, pop_memory_notification
from agent.memory.markdown_store import MarkdownMemoryStore
from agent.memory.safety import MemorySafetyPolicy
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.llm import LLMResponse


def test_extractor_writes_high_confidence_repeated_preference_and_notifies(tmp_path):
    service, store, llm, context = _service(
        tmp_path,
        {
            "memories": [
                {
                    "category": "preferences",
                    "content": "回答时先给结论，最多列三点。",
                    "confidence": "high",
                    "evidence": [
                        {"turn_index": 1, "quote": "先给结论，最多三点"},
                        {"turn_index": 3, "quote": "先给结论，最多三点"},
                    ],
                }
            ]
        },
    )

    result = service.extract("alpha", _messages(3), llm)

    assert len(result.added) == 1
    assert store.search(category="preferences")[0].content == "回答时先给结论，最多列三点。"
    assert pop_memory_notification(context) == ("回答时先给结论，最多列三点。",)
    assert pop_memory_notification(context) == ()


def test_extractor_requires_three_user_turns_without_calling_llm(tmp_path):
    service, store, llm, _context = _service(tmp_path, {"memories": []})

    result = service.extract("alpha", _messages(2), llm)

    assert result.reviewed_through_turn_index == 0
    assert llm.calls == 0
    assert store.count() == 0


def test_extractor_rejects_weak_or_unverifiable_evidence(tmp_path):
    service, store, llm, _context = _service(
        tmp_path,
        {
            "memories": [
                {
                    "category": "preferences",
                    "content": "回答简短。",
                    "confidence": "medium",
                    "evidence": [
                        {"turn_index": 1, "quote": "先给结论"},
                        {"turn_index": 2, "quote": "最多三点"},
                    ],
                },
                {
                    "category": "preferences",
                    "content": "回答时先给结论。",
                    "confidence": "high",
                    "evidence": [
                        {"turn_index": 1, "quote": "不存在的原文"},
                        {"turn_index": 2, "quote": "最多三点"},
                    ],
                },
            ]
        },
    )

    result = service.extract("alpha", _messages(3), llm)

    assert result.added == ()
    assert store.count() == 0


def test_extractor_checkpoint_prevents_reprocessing_same_turns(tmp_path):
    service, store, llm, _context = _service(tmp_path, {"memories": []})

    first = service.extract("alpha", _messages(3), llm)
    second = service.extract("alpha", _messages(3), llm)

    assert first.reviewed_through_turn_index == 3
    assert second.reviewed_through_turn_index == 3
    assert llm.calls == 1
    assert store.count() == 0


def test_extractor_exposes_provider_failure_as_retryable_code(tmp_path):
    service, _store, _llm, _context = _service(tmp_path, {"memories": []})

    with pytest.raises(MemoryStoreError) as caught:
        service.extract("alpha", _messages(3), _FailingLlm())

    assert caught.value.code == "MEMORY_EXTRACTION_PROVIDER_FAILED"


def test_extractor_reports_missing_built_in_prompt(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    context = build_memory_context(
        tmp_path / "memory",
        scope="workspace",
        actor_user_id=None,
    )
    service = MemoryExtractionService(
        context,
        MarkdownMemoryStore(context),
        PromptLoader(prompts),
        MemorySafetyPolicy(),
    )
    llm = _Llm({"memories": []})

    with pytest.raises(MemoryStoreError) as caught:
        service.extract("alpha", _messages(3), llm)

    assert caught.value.code == "MEMORY_EXTRACTION_PROMPT_NOT_FOUND"
    assert llm.calls == 0


def test_extractor_does_not_commit_cancelled_result(tmp_path):
    service, store, llm, context = _service(
        tmp_path,
        {
            "memories": [
                {
                    "category": "preferences",
                    "content": "回答时先给结论。",
                    "confidence": "high",
                    "evidence": [
                        {"turn_index": 1, "quote": "先给结论"},
                        {"turn_index": 2, "quote": "先给结论"},
                    ],
                }
            ]
        },
    )

    result = service.extract(
        "alpha",
        _messages(3),
        llm,
        should_commit=lambda: False,
    )

    assert result.added == ()
    assert result.reviewed_through_turn_index == 0
    assert store.count() == 0
    assert pop_memory_notification(context) == ()


def _service(tmp_path, payload):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "memory_extraction.md").write_text("extract", encoding="utf-8")
    context = build_memory_context(
        tmp_path / "memory",
        scope="workspace",
        actor_user_id=None,
    )
    store = MarkdownMemoryStore(context)
    llm = _Llm(payload)
    return (
        MemoryExtractionService(
            context,
            store,
            PromptLoader(prompts),
            MemorySafetyPolicy(),
        ),
        store,
        llm,
        context,
    )


def _messages(count: int) -> list[Message]:
    messages = []
    for index in range(1, count + 1):
        messages.extend(
            [
                Message(
                    role="user",
                    content="先给结论，最多三点",
                    turn_id=f"turn-{index}",
                    turn_index=index,
                ),
                Message(
                    role="assistant",
                    content="好的",
                    turn_id=f"turn-{index}",
                    turn_index=index,
                ),
            ]
        )
    return messages


class _Llm:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_messages = None

    def chat(self, messages, tools=None):
        self.calls += 1
        self.last_messages = messages
        return LLMResponse(content=json.dumps(self.payload, ensure_ascii=False))


class _FailingLlm:
    def chat(self, messages, tools=None):
        raise TimeoutError("provider timeout")


def test_extractor_fits_source_turns_to_context_budget(tmp_path):
    from agent.protocols.llm import ContextBudget

    service, _store, llm, _context = _service(tmp_path, {"memories": []})
    messages = _messages(8)
    for message in messages:
        if message.role == "user":
            message.content += " x" * 1000

    service.extract(
        "alpha",
        messages,
        llm,
        context_budget=ContextBudget(input_token_limit=900),
    )

    assert llm.calls == 1
    assert len(llm.last_messages[1]["content"]) < 4000
