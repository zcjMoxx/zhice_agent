from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_travel_prompt_and_skill_publish_optimizer_feasibility_contract() -> None:
    prompt = (ROOT / "prompts" / "travel_planning.md").read_text(encoding="utf-8")
    skill = (
        ROOT / "skill_repo" / "skills" / "travel-planner" / "SKILL.md"
    ).read_text(encoding="utf-8")

    shared_rules = (
        "relaxed=480",
        "balanced=600",
        "intensive=720",
        "活动数 * 0.35",
        "路线总距离公里 / 80",
    )
    for rule in shared_rules:
        assert rule in prompt
        assert rule in skill

    assert "relaxed<=9" in prompt
    assert "balanced<=11" in prompt
    assert "intensive<=13" in prompt
    assert "rejected_candidates[].reasons" in prompt
    assert "最多重试一次" in prompt
    assert "第二次仍失败" in prompt
    assert "不要继续调用 Skill" in prompt
    assert "rejected_candidates[].reasons" in skill
    assert "再次失败则停止" in skill


def test_travel_prompt_and_skill_publish_finalize_nested_allowlists() -> None:
    prompt = (ROOT / "prompts" / "travel_planning.md").read_text(encoding="utf-8")
    skill = (
        ROOT / "skill_repo" / "skills" / "travel-planner" / "SKILL.md"
    ).read_text(encoding="utf-8")

    for field in ("planning_mode", "content_hash", "retrieved_at", "source_type"):
        assert field in prompt
        assert field in skill
    assert "不要传 `mode`" in prompt
    assert "metadata" in prompt
    assert "临时别名" in skill
    assert "total_minutes" in prompt
    assert "RFC 3339" in prompt
    assert "quality gate" in skill
    assert "RFC 3339" in skill
    assert "只有 `model_estimate`" in prompt
    assert "无 URL 的外部结果" in skill
    assert "六十四位小写十六进制" in prompt
    assert "official_api -> live|historical|unknown" in skill


def test_travel_prompt_requires_real_terminal_state_and_non_blocking_optional_fields() -> None:
    prompt = (ROOT / "prompts" / "travel_planning.md").read_text(encoding="utf-8")

    assert "request_travel_clarification" in prompt
    assert "不得只汇报" in prompt
    assert "finalize_travel_plan" in prompt
    assert "人群、预算、偏好、兴趣、节奏和模式未指定时不阻塞" in prompt
    assert 'name="zhice-official/travel-planner"' in prompt
    assert "不要再同时传 `source`" in prompt


def test_requirement_prompt_keeps_travel_assistant_scope_and_handoff_intents() -> None:
    prompt = (ROOT / "prompts" / "travel_requirement_extraction.md").read_text(encoding="utf-8")

    for intent in (
        "travel_requirement",
        "assistant_greeting",
        "assistant_identity",
        "assistant_capabilities",
        "planner_help",
        "unrelated",
    ):
        assert intent in prompt
    assert "不进行自由聊天" in prompt
    assert "智策旅行助手" in prompt


def test_intake_prompt_requires_natural_reply_bounded_tools_and_no_early_research() -> None:
    prompt = (ROOT / "prompts" / "travel_intake.md").read_text(encoding="utf-8")

    assert "智策旅行助手" in prompt
    assert "update_travel_draft" in prompt
    assert "offer_main_chat_handoff" in prompt
    assert "confirm_and_start_travel_planning" in prompt
    assert "不得只口头回复已经开始" in prompt
    assert "每轮最多优先追问一到两个" in prompt
    assert "不回答其实质内容" in prompt
    assert "不声称已经查询实时天气" in prompt
    assert "不主动解释模型、系统模式或内部架构" in prompt
