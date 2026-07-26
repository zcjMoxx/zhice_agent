from agent.context.history_query import (
    LLMHistoryQueryPlanner,
    SessionHistoryQueryResolver,
    format_history_evidence,
)
from agent.core.turns import group_messages_by_turn
from agent.message import Message
from agent.protocols.llm import LLMResponse


def _turn(index: int, user: str, assistant: str = "") -> list[Message]:
    turn_id = f"turn-{index}"
    return [
        Message(role="user", content=user, turn_id=turn_id, turn_index=index),
        Message(role="assistant", content=assistant, turn_id=turn_id, turn_index=index),
    ]


def test_history_query_scans_action_target_without_embedding():
    turns = group_messages_by_turn(
        [
            *_turn(1, "你好"),
            *_turn(2, "介绍一下牛顿", "牛顿是英国物理学家。"),
            *_turn(3, "解释 Python"),
        ]
    )

    result = SessionHistoryQueryResolver().resolve("我之前让我介绍过谁？", turns)

    assert result is not None
    assert result.plan_type == "match_action"
    assert result.matched_turn_indexes == (2,)
    assert "介绍一下牛顿" in format_history_evidence(result)


def test_history_query_counts_and_lists_exact_user_turns():
    turns = group_messages_by_turn([*_turn(1, "问题一"), *_turn(2, "问题二")])

    count = SessionHistoryQueryResolver().resolve("我问了几个问题？", turns)
    listing = SessionHistoryQueryResolver().resolve("我之前问了什么？", turns)

    assert count is not None and count.total_user_turns == 2
    assert "2 个用户 Turn" in count.direct_answer
    assert listing is not None
    assert [item["user_text"] for item in listing.evidence] == ["问题一", "问题二"]


def test_non_history_question_is_not_intercepted():
    turns = group_messages_by_turn(_turn(1, "介绍一下牛顿"))
    assert SessionHistoryQueryResolver().resolve("介绍一下爱因斯坦", turns) is None


def test_llm_fallback_can_only_plan_then_deterministic_executor_scans_current_turns():
    class PlannerLLM:
        def chat(self, messages, tools=None):
            return LLMResponse(content='{"type":"before","anchor":"问题二","session_id":"other"}')

    turns = group_messages_by_turn(
        [*_turn(1, "问题一"), *_turn(2, "问题二"), *_turn(3, "问题三")]
    )
    plan = LLMHistoryQueryPlanner(PlannerLLM(), "plan only").plan("查询历史")
    result = SessionHistoryQueryResolver().execute_plan(plan or {}, turns)

    assert plan == {"type": "before", "anchor": "问题二"}
    assert result is not None
    assert result.matched_turn_indexes == (1,)
