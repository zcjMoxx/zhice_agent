import io

import agent.console as console_module


def test_supervisor_force_color_restores_console_styles_for_pipe(monkeypatch):
    monkeypatch.setenv("ZHICE_FORCE_TERMINAL_COLOR", "1")
    monkeypatch.setattr(console_module.sys, "stdout", io.StringIO())
    monkeypatch.setattr(console_module, "_COLOR_ENABLED", None)

    assert console_module.console.command("http://127.0.0.1:10086") == (
        "\033[36mhttp://127.0.0.1:10086\033[0m"
    )


def test_no_color_disables_forced_console_styles(monkeypatch):
    monkeypatch.setenv("ZHICE_FORCE_TERMINAL_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(console_module, "_COLOR_ENABLED", None)

    assert console_module.console.command("http://127.0.0.1:10086") == (
        "http://127.0.0.1:10086"
    )


def test_auto_color_detection_does_not_leak_between_output_environments(monkeypatch):
    monkeypatch.setattr(console_module, "_COLOR_ENABLED", None)
    monkeypatch.setenv("ZHICE_FORCE_TERMINAL_COLOR", "1")

    assert console_module.console.command("first") == "\033[36mfirst\033[0m"

    monkeypatch.delenv("ZHICE_FORCE_TERMINAL_COLOR")
    monkeypatch.setattr(console_module.sys, "stdout", io.StringIO())

    assert console_module.console.command("second") == "second"
