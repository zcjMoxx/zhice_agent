from agent.protocols.capability import CapabilityStatus
from agent.subagents.presentation import format_subagent_unavailable


def test_human_unavailable_message_keeps_cause_without_machine_payload():
    text = format_subagent_unavailable(
        CapabilityStatus(
            name="subagent",
            state="unavailable",
            code="SUBAGENT_PROMPT_NOT_FOUND",
            message="Required Subagent runtime prompt is missing: subagent.md",
            hint="Run zcagent init, then restart the process.",
        ),
        include_details=True,
    )

    assert text == (
        "Subagent is currently unavailable: Required Subagent runtime prompt is missing: "
        "subagent.md Run zcagent init, then restart the process."
    )
    assert "SUBAGENT_PROMPT_NOT_FOUND" not in text
    assert "cause_code" not in text


def test_human_unavailable_message_has_safe_fallback():
    assert format_subagent_unavailable(None, include_details=True) == (
        "Subagent is currently unavailable: Subagent runtime is unavailable."
    )


def test_human_unavailable_message_hides_details_for_ordinary_user():
    text = format_subagent_unavailable(
        CapabilityStatus(
            name="subagent",
            state="unavailable",
            code="SUBAGENT_PROMPT_NOT_FOUND",
            message="Required Subagent runtime prompt is missing: subagent.md",
            hint="Run zcagent init, then restart the process.",
        ),
        include_details=False,
    )

    assert text == "Subagent is temporarily unavailable. Please contact an administrator."
    assert "subagent.md" not in text
    assert "zcagent init" not in text
