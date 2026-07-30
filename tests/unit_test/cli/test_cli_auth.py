from __future__ import annotations

import pytest

from agent.auth.store import SQLiteAuthStore
from agent.cli import main


def test_cli_auth_init_owner_creates_unique_owner_without_password_argument(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ZHICE_AGENT_SETUP_TOKEN", "setup-secret")
    passwords = iter(["setup-secret", "password-123"])
    monkeypatch.setattr("agent.cli.getpass.getpass", lambda _prompt: next(passwords))

    result = main(
        [
            "auth",
            "--workspace",
            str(tmp_path),
            "init-owner",
            "--username",
            "root",
            "--display-name",
            "Root User",
        ]
    )

    assert result == 0
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    actor = store.authenticate("root", "password-123", channel="web")
    assert actor is not None
    assert "owner" in actor.role_keys
    assert not (tmp_path / "contexts" / "users" / actor.user_id).exists()
    assert "owner initialized" in capsys.readouterr().out


def test_cli_auth_init_owner_refuses_second_bootstrap(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZHICE_AGENT_SETUP_TOKEN", "setup-secret")
    passwords = iter(["setup-secret", "password-123"])
    monkeypatch.setattr("agent.cli.getpass.getpass", lambda _prompt: next(passwords))
    first = main(["auth", "--workspace", str(tmp_path), "init-owner"])
    second = main(
        ["auth", "--workspace", str(tmp_path), "init-owner", "--username", "second"]
    )

    assert first == 0
    assert second == 1
    assert "already exist" in capsys.readouterr().out


def test_cli_auth_init_owner_checks_existing_owner_before_reading_password(tmp_path, monkeypatch, capsys):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    monkeypatch.setattr(
        "agent.cli.getpass.getpass",
        lambda _prompt: pytest.fail("password prompt must not be shown when Owner already exists"),
    )

    result = main(["auth", "--workspace", str(tmp_path), "init-owner"])

    assert result == 1
    assert "owner already exists" in capsys.readouterr().out


def test_cli_auth_init_owner_rejects_invalid_setup_token_before_reading_password(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("ZHICE_AGENT_SETUP_TOKEN", "setup-secret")
    prompts = iter(["wrong-secret"])
    monkeypatch.setattr("agent.cli.getpass.getpass", lambda _prompt: next(prompts))

    result = main(["auth", "--workspace", str(tmp_path), "init-owner"])

    assert result == 1
    assert "Invalid setup credential" in capsys.readouterr().out


def test_cli_auth_init_owner_requires_configured_setup_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ZHICE_AGENT_SETUP_TOKEN", raising=False)
    monkeypatch.setattr("agent.cli.bootstrap_dotenv", lambda _env_file, **_kwargs: None)
    monkeypatch.setattr(
        "agent.cli.getpass.getpass",
        lambda _prompt: pytest.fail("setup token must be configured before prompting"),
    )

    result = main(["auth", "--workspace", str(tmp_path), "init-owner"])

    assert result == 1
    assert "Owner setup is disabled" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["init-admin", "import-cli-session"])
def test_cli_auth_rejects_removed_compatibility_commands(command):
    with pytest.raises(SystemExit, match="2"):
        main(["auth", command])
