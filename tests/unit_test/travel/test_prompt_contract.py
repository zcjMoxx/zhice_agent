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
    assert "TRAVEL_SOURCE_ALREADY_QUERIED" in prompt
    assert "TRAVEL_SOURCE_BUDGET_EXHAUSTED" in prompt
    assert "price_source_evidence_ids" in prompt
    assert "景点、博物馆或普通酒店 POI" in prompt
    assert "ToolResult 返回的 `station_code` 原样传给 `get-tickets`" in prompt
    assert "跨城铁路只进入最终顶层 `transport_plan`" in prompt

    skill = (
        ROOT / "skill_repo" / "skills" / "travel-planner" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "原样使用其中的 `station_code`" in skill
    assert "不得把高德跨城公交结果写成高铁" in skill


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
    assert "智策旅行顾问" in prompt


def test_intake_prompt_requires_natural_reply_bounded_tools_and_no_early_research() -> None:
    prompt = (ROOT / "prompts" / "travel_intake.md").read_text(encoding="utf-8")

    assert "智策旅行顾问" in prompt
    assert "update_travel_draft" in prompt
    assert "offer_main_chat_handoff" in prompt
    assert "confirm_and_start_travel_planning" in prompt
    assert "不得只口头回复已经开始" in prompt
    assert "每轮最多优先追问一到两个" in prompt
    assert "不回答其实质内容" in prompt
    assert "不声称已经查询实时天气" in prompt
    assert "不主动解释模型、系统模式或内部架构" in prompt
    assert "必须首先明确说“请点击页面上的「确认并开始规划」即可立即开始”" in prompt
    assert "也不要列出“确认/开始执行/开始规划”" in prompt
    assert "必须首先明确说" in prompt
    assert "禁止说“可以直接回复确认或开始执行，我就开始规划”" in prompt
    assert "确认前禁止输出 A/B/C" in prompt
    assert "不得抢跑生成无来源预方案" in prompt
    assert "location_clarifications" in prompt
    assert "地点唯一性也是开始规划的必要条件" in prompt


def test_travel_prompts_require_concrete_stays_and_weather_provenance() -> None:
    planning = (ROOT / "prompts" / "travel_planning.md").read_text(encoding="utf-8")
    continuation = (ROOT / "prompts" / "travel_planning_continuation.md").read_text(
        encoding="utf-8"
    )

    for prompt in (planning, continuation):
        assert "TRAVEL_STAY_REQUIRED" in prompt
        assert "TRAVEL_WEATHER_EVIDENCE_MISSING" in prompt
        assert "具体酒店" in prompt
        assert "provider" in prompt
        assert "freshness" in prompt
        assert "TRAVEL_ROUTE_EVIDENCE_MISSING" in prompt
        assert "不少于 2 公里" in prompt
        assert "返程列车" in prompt

    assert "澄清前禁止调用地图、天气、交通、酒店、网页或社区来源去猜地点" in planning


def test_travel_prompts_require_parallel_research_and_targeted_finalization() -> None:
    planning = (ROOT / "prompts" / "travel_planning.md").read_text(encoding="utf-8")
    continuation = (ROOT / "prompts" / "travel_planning_continuation.md").read_text(
        encoding="utf-8"
    )
    skill = (
        ROOT / "skill_repo" / "skills" / "travel-planner" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "quick 与 deep 模式" in planning
    assert "`travel-transport-weather`" in planning
    assert "`travel-stay-poi`" in planning
    assert "`travel-guides`" in planning
    assert "预选景点种子" in planning
    assert "`洛阳攻略` 仍是城市级查询" in planning
    assert "search_travel_hotels" in planning
    assert "不得重新查询整套" in planning
    assert "禁止重新运行 optimizer 或重查天气" in continuation
    assert "TRAVEL_HOTEL_PRICE_EVIDENCE_MISSING" in skill
