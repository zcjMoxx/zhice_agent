from agent.mcp.naming import local_tool_name


def test_local_name_is_safe_stable_and_bounded():
    first = local_tool_name("mail server", "send/message with a very long remote name " * 4)
    second = local_tool_name("mail server", "send/message with a very long remote name " * 4)

    assert first == second
    assert first.startswith("mcp__mail_server__")
    assert len(first) <= 64
    assert all(character.isalnum() or character in "_-" for character in first)
