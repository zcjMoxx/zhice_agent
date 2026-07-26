import json

from agent.context.compaction import CompactionStore
from agent.context.config import (
    CompactionConfig,
    ContextEngineeringConfig,
    FullHistoryConfig,
    RetrievalConfig,
)
from agent.context.planner import (
    _incremental_compaction_prefixes,
    _planned_recent_budget,
    _retrieval_query,
    _safe_limit,
)
from agent.core.context import ContextBuilder, estimate_llm_tokens
from agent.core.turns import assign_turn, group_messages_by_turn
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.llm import ContextBudget, LLMResponse
from agent.session.jsonl_store import JsonlSessionStore


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        payload = {field: [] for field in (
            "topics", "user_questions", "entities", "decisions", "confirmed_facts",
            "unresolved_items", "constraints", "files_and_errors", "tool_result_references",
        )}
        return LLMResponse(content=json.dumps(payload))


class FailingEmbedding:
    identity = "fake:failing"
    batch_size = 16

    def embed(self, texts):
        raise TimeoutError("offline")


class FailingLLM:
    def chat(self, messages, tools=None):
        raise TimeoutError("offline")


def _prompts(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    for name in ("identity", "tool_use_policy", "skills_intro"):
        (prompts / f"{name}.md").write_text(name, encoding="utf-8")
    source = __import__("pathlib").Path(__file__).resolve().parents[3] / "prompts" / "context_compaction.md"
    (prompts / "context_compaction.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return prompts


def _history(count, size=20):
    messages = []
    for index in range(1, count + 1):
        messages.extend(
            [
                Message(role="user", content=f"question {index} " + "x" * size, turn_id=f"turn-{index}", turn_index=index),
                Message(role="assistant", content=f"answer {index} " + "y" * size, turn_id=f"turn-{index}", turn_index=index),
            ]
        )
    return messages


def test_full_mode_includes_all_budget_fitting_turns(tmp_path, caplog):
    builder = ContextBuilder(PromptLoader(_prompts(tmp_path)))
    history = _history(10)
    caplog.set_level("DEBUG", logger="zcagent.agent.context")

    messages = builder.build(history=history, user_message=Message(role="user", content="current"), workspace=tmp_path, session_id="session")

    assert builder.last_plan.mode == "full"
    assert len(builder.last_plan.selected_turn_ids) == 10
    assert any(message.get("content", "").startswith("question 1 ") for message in messages)
    selection = next(record for record in caplog.records if record.event == "context.selection")
    assert selection.fields["mode"] == "full"
    assert selection.fields["candidate_turn_count"] == 10
    assert "question 1" not in str(selection.fields)


def test_long_mode_compacts_and_degrades_when_embedding_fails(tmp_path):
    config = ContextEngineeringConfig(
        full_history=FullHistoryConfig(turn_growth_reserve_tokens=20, turn_growth_reserve_ratio=0.0),
        compaction=CompactionConfig(
            recent_keep_ratio=0.2,
            post_compaction_max_ratio=0.6,
            min_recent_turns=2,
        ),
        retrieval=RetrievalConfig(top_k=2),
    )
    builder = ContextBuilder(PromptLoader(_prompts(tmp_path)), context_config=config, embedding_provider=FailingEmbedding())
    store = JsonlSessionStore(tmp_path / "contexts" / "sessions")
    history = _history(12, size=120)

    messages = builder.build(
        history=history,
        user_message=Message(role="user", content="question 1 again"),
        workspace=tmp_path,
        session_id="session",
        context_budget=ContextBudget(input_token_limit=700),
        session_store=store,
        llm_provider=FakeLLM(),
    )

    assert builder.last_plan.mode == "compacted_retrieval"
    assert builder.last_plan.compaction_id.startswith("compact-")
    assert builder.last_plan.compacted_through_turn_index == (
        len(group_messages_by_turn(history)) - len(builder.last_plan.recent_turn_ids)
    )
    assert "embedding_backfill" in builder.last_plan.degraded
    assert messages[-1]["content"] == "question 1 again"
    assert (tmp_path / "contexts" / "context" / "compactions" / "session.json").is_file()


def test_clear_removes_compaction_and_index_but_not_session_truth(tmp_path):
    builder = ContextBuilder(PromptLoader(_prompts(tmp_path)))
    store = JsonlSessionStore(tmp_path / "contexts" / "sessions")
    messages = _history(1)
    store.append("session", messages)
    builder.on_turn_committed(store, "session", messages)
    context_root = tmp_path / "contexts" / "context"
    compactions = context_root / "compactions"
    compactions.mkdir(parents=True)
    (compactions / "session.json").write_text("{}", encoding="utf-8")

    builder.delete_derived_session(store, "session")

    assert store.load("session").messages
    assert not (compactions / "session.json").exists()
    from agent.context.index import SQLiteTurnSearchIndex
    assert SQLiteTurnSearchIndex(context_root / "context_index.sqlite3").search("session", "question") == []


def test_compaction_work_is_split_into_bounded_incremental_prefixes():
    turns = group_messages_by_turn(_history(70))
    prefixes = _incremental_compaction_prefixes(turns, None)

    assert [len(prefix) for prefix in prefixes] == [32, 64, 70]


def test_default_compaction_trigger_is_eighty_five_percent():
    assert _safe_limit(1000, ContextEngineeringConfig()) == 850


def test_first_compaction_reserves_summary_before_selecting_recent_raw():
    planned, reserve = _planned_recent_budget(
        recent_budget=3000,
        post_compaction_max=7000,
        fixed_tokens=2613,
        record=None,
        estimate_tokens=lambda messages: 0,
    )

    assert reserve == 3000
    assert planned == 1387


def test_low_watermark_does_not_cause_a_second_compaction_call(tmp_path):
    config = ContextEngineeringConfig(
        compaction=CompactionConfig(
            trigger_budget_ratio=0.85,
            recent_keep_ratio=0.15,
            post_compaction_max_ratio=0.35,
            min_recent_turns=1,
        ),
        retrieval=RetrievalConfig(enabled=False),
    )
    llm = FakeLLM()
    builder = ContextBuilder(
        PromptLoader(_prompts(tmp_path)),
        context_config=config,
    )
    store = JsonlSessionStore(tmp_path / "contexts" / "sessions")

    builder.build(
        history=_history(19, size=500),
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="session",
        context_budget=ContextBudget(input_token_limit=2000),
        session_store=store,
        llm_provider=llm,
    )

    assert builder.last_plan.mode == "compacted_retrieval"
    assert llm.calls == 1


def test_existing_compaction_is_reused_until_tail_reaches_trigger_waterline(tmp_path):
    config = ContextEngineeringConfig(
        compaction=CompactionConfig(
            trigger_budget_ratio=0.8,
            recent_keep_ratio=0.2,
            post_compaction_max_ratio=0.6,
            min_recent_turns=2,
        ),
        retrieval=RetrievalConfig(enabled=False),
    )
    llm = FakeLLM()
    prompts = _prompts(tmp_path)
    builder = ContextBuilder(PromptLoader(prompts), context_config=config)
    store = JsonlSessionStore(tmp_path / "contexts" / "sessions")
    first_history = _history(12, size=120)

    builder.build(
        history=first_history,
        user_message=Message(role="user", content="first overflow"),
        workspace=tmp_path,
        session_id="session",
        context_budget=ContextBudget(input_token_limit=700),
        session_store=store,
        llm_provider=llm,
    )
    first_calls = llm.calls
    first_compaction_id = builder.last_plan.compaction_id
    tail = [
        assign_turn(
            Message(role="user", content="small tail question"),
            turn_id="turn-13",
            turn_index=13,
        ),
        assign_turn(
            Message(role="assistant", content="small tail answer"),
            turn_id="turn-13",
            turn_index=13,
        ),
    ]

    builder.build(
        history=[*first_history, *tail],
        user_message=Message(role="user", content="small raw tail"),
        workspace=tmp_path,
        session_id="session",
        context_budget=ContextBudget(input_token_limit=700),
        session_store=store,
        llm_provider=llm,
    )

    assert llm.calls == first_calls
    assert builder.last_plan.compaction_id == first_compaction_id


def test_short_self_contained_retrieval_query_is_not_polluted_by_recent_turns():
    recent = group_messages_by_turn(_history(2, size=120))

    assert _retrieval_query("项目测试口令是什么？", recent) == "项目测试口令是什么？"
    assert "question 2" in _retrieval_query("继续说这个", recent)


def test_background_precompaction_uses_dedicated_provider_once(tmp_path, monkeypatch):
    class ImmediateThread:
        def __init__(self, *, target, args, **kwargs):
            del kwargs
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("agent.core.context.threading.Thread", ImmediateThread)
    config = ContextEngineeringConfig(
        compaction=CompactionConfig(
            trigger_budget_ratio=0.85,
            recent_keep_ratio=0.15,
            post_compaction_max_ratio=0.35,
            background_enabled=True,
            background_trigger_budget_ratio=0.80,
            min_recent_turns=1,
        ),
        retrieval=RetrievalConfig(enabled=False),
    )
    main_llm = FakeLLM()
    compaction_llm = FakeLLM()
    prompts = _prompts(tmp_path)
    builder = ContextBuilder(
        PromptLoader(prompts),
        context_config=config,
        compaction_llm_provider=compaction_llm,
    )
    store = JsonlSessionStore(tmp_path / "contexts" / "sessions")
    history = _history(12, size=120)
    store.append("session", history)
    system = {
        "role": "system",
        "content": builder._build_system_prompt(tmp_path, "session"),
    }
    committed_tokens = estimate_llm_tokens(
        [system, *builder._history_to_llm_dicts(history)]
    )
    budget = ContextBudget(input_token_limit=max(1, int(committed_tokens / 0.81)))

    builder.build(
        history=history,
        user_message=Message(role="user", content="small current"),
        workspace=tmp_path,
        session_id="session",
        context_budget=budget,
        session_store=store,
        llm_provider=main_llm,
    )
    assert builder.last_plan.mode == "full"

    builder.on_turn_committed(store, "session", [])

    record = CompactionStore(
        tmp_path / "contexts" / "context" / "compactions"
    ).load("session")
    assert record is not None
    assert compaction_llm.calls == 1
    assert main_llm.calls == 0

    large_tail = assign_turn(
        Message(role="user", content="z" * 1000),
        turn_id="turn-13",
        turn_index=13,
    )
    store.append("session", [large_tail])
    builder.build(
        history=[*history, large_tail],
        user_message=Message(role="user", content="next"),
        workspace=tmp_path,
        session_id="session",
        context_budget=budget,
        session_store=store,
        llm_provider=main_llm,
    )

    assert builder.last_plan.compaction_id == record.compaction_id
    assert compaction_llm.calls == 1


def test_background_precompaction_stays_idle_below_eighty_percent(tmp_path):
    config = ContextEngineeringConfig(retrieval=RetrievalConfig(enabled=False))
    compaction_llm = FakeLLM()
    builder = ContextBuilder(
        PromptLoader(_prompts(tmp_path)),
        context_config=config,
        compaction_llm_provider=compaction_llm,
    )
    store = JsonlSessionStore(tmp_path / "contexts" / "sessions")
    history = _history(8, size=80)
    store.append("session", history)
    system = {
        "role": "system",
        "content": builder._build_system_prompt(tmp_path, "session"),
    }
    committed_tokens = estimate_llm_tokens(
        [system, *builder._history_to_llm_dicts(history)]
    )
    budget = ContextBudget(input_token_limit=max(1, int(committed_tokens / 0.79)))
    builder.build(
        history=history,
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="session",
        context_budget=budget,
        session_store=store,
        llm_provider=FakeLLM(),
    )

    builder.on_turn_committed(store, "session", [])

    assert compaction_llm.calls == 0


def test_background_precompaction_failure_does_not_escape_commit(
    tmp_path,
    monkeypatch,
    caplog,
):
    class ImmediateThread:
        def __init__(self, *, target, args, **kwargs):
            del kwargs
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("agent.core.context.threading.Thread", ImmediateThread)
    config = ContextEngineeringConfig(retrieval=RetrievalConfig(enabled=False))
    builder = ContextBuilder(
        PromptLoader(_prompts(tmp_path)),
        context_config=config,
        compaction_llm_provider=FailingLLM(),
    )
    store = JsonlSessionStore(tmp_path / "contexts" / "sessions")
    history = _history(12, size=120)
    store.append("session", history)
    system = {
        "role": "system",
        "content": builder._build_system_prompt(tmp_path, "session"),
    }
    committed_tokens = estimate_llm_tokens(
        [system, *builder._history_to_llm_dicts(history)]
    )
    budget = ContextBudget(input_token_limit=max(1, int(committed_tokens / 0.81)))
    builder.build(
        history=history,
        user_message=Message(role="user", content="current"),
        workspace=tmp_path,
        session_id="session",
        context_budget=budget,
        session_store=store,
        llm_provider=FakeLLM(),
    )
    caplog.set_level("WARNING", logger="zcagent.agent.context")

    builder.on_turn_committed(store, "session", [])

    assert any(
        record.event == "context.compaction.background_failed"
        for record in caplog.records
    )
    assert CompactionStore(
        tmp_path / "contexts" / "context" / "compactions"
    ).load("session") is None
