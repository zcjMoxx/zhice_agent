"""Tests for exec command policy helpers."""

from agent.tools.shell_policy import redact_secrets, validate_command


def test_validate_command_allows_simple_local_commands():
    """Simple single commands should be allowed."""

    result = validate_command("python -m pytest")

    assert result.allowed is True
    assert result.code == "OK"


def test_validate_command_rejects_missing_and_too_long_commands():
    """Empty and huge commands should not reach subprocess."""

    missing = validate_command("   ")
    too_long = validate_command("x" * 2001)

    assert missing.allowed is False
    assert missing.code == "MISSING_PARAM"
    assert too_long.allowed is False
    assert too_long.code == "COMMAND_TOO_LONG"


def test_validate_command_rejects_complex_shell_syntax():
    """The first exec version should not support command chaining."""

    for command in [
        "python -m pytest && python -m ruff check .",
        "python -m pytest | more",
        "python -m pytest > out.txt",
        "python -m pytest; python -m ruff check .",
        "echo $(whoami)",
    ]:
        result = validate_command(command)

        assert result.allowed is False
        assert result.code == "UNSUPPORTED_SHELL_SYNTAX"


def test_validate_command_allows_semicolons_inside_quotes():
    """Python one-liners often use semicolons inside the quoted script."""

    result = validate_command('python -c "import sys; sys.exit(0)"')

    assert result.allowed is True


def test_validate_command_classifies_workspace_bounded_destructive_commands_for_confirmation():
    """Known cwd-bounded destructive commands may enter the explicit confirmation flow."""

    for command in [
        "rm -rf .",
        "del /s file.txt",
        "Remove-Item -Recurse .",
        "git reset --hard HEAD",
        "git clean -fd",
        "git checkout -- README.md",
    ]:
        result = validate_command(command)

        assert result.allowed is True
        assert result.requires_confirmation is True
        assert result.required_permission == "tool.exec.dangerous"
        assert result.risk_category == "destructive"


def test_validate_command_classifies_network_and_install_commands_for_confirmation():
    """Known network/install commands require dangerous permission and confirmation."""

    for command in [
        "curl https://example.com",
        "Invoke-WebRequest https://example.com",
        "python -m pip install requests",
        "npm install",
        "yarn add vite",
        "git clone https://example.com/repo.git",
    ]:
        result = validate_command(command)

        assert result.allowed is True
        assert result.requires_confirmation is True
        assert result.required_permission == "tool.exec.dangerous"
        assert result.risk_category == "network"


def test_validate_command_keeps_absolute_destructive_paths_blocked():
    result = validate_command(r"Remove-Item -Recurse C:\\Users\\Public")

    assert result.allowed is False
    assert result.code == "PATH_OUTSIDE_WORKSPACE"


def test_validate_command_blocks_absolute_paths_even_for_read_commands():
    for command in [
        r"Get-Content C:\\Users\\Public\\notes.txt",
        "cat /etc/passwd",
        r'''python -c "print(open('C:\\Users\\Public\\notes.txt').read())"''',
        r"Get-Content $env:USERPROFILE\\notes.txt",
    ]:
        result = validate_command(command)

        assert result.allowed is False
        assert result.code == "PATH_OUTSIDE_WORKSPACE"


def test_validate_command_blocks_environment_dump_commands():
    """Bulk environment dumps could expose secrets."""

    for command in ["env", "printenv", "set", "Get-ChildItem Env:", "gci env:"]:
        result = validate_command(command)

        assert result.allowed is False
        assert result.code == "ENV_DUMP_BLOCKED"


def test_redact_secrets_masks_common_secret_shapes():
    """Output redaction is a second safety layer after command blocking."""

    text = "\n".join(
        [
            "OPENAI_API_KEY=sk-testsecret123456",
            "Authorization: Bearer abc.def.ghi",
            "password: hunter2",
        ]
    )

    redacted = redact_secrets(text)

    assert "sk-testsecret123456" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "hunter2" not in redacted
    assert "OPENAI_API_KEY=<redacted>" in redacted
    assert "Authorization: Bearer <redacted>" in redacted
