"""Tests for terminal spinner rendering."""

from __future__ import annotations


class _TtyBuffer:
    def __init__(self):
        self.parts: list[str] = []

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        return None

    @property
    def text(self) -> str:
        return "".join(self.parts)


def test_spinner_shorter_final_line_erases_previous_frame_tail(monkeypatch):
    """A final line shorter than the animated frame should clear old characters."""

    from agent.console import Spinner

    buffer = _TtyBuffer()
    monkeypatch.setattr("sys.stderr", buffer)
    spinner = Spinner("thinking")

    spinner._write_status("⠋ thinking... 3.9s", len("⠋ thinking... 3.9s"))
    spinner._write_status("⠿ thinking 3.9s", len("⠿ thinking 3.9s"))

    assert buffer.parts[-1].endswith("   ")
    assert buffer.text.endswith("\r⠿ thinking 3.9s   ")


def test_spinner_runtime_label_can_be_updated():
    from agent.console import Spinner

    spinner = Spinner("已接收问题")
    spinner.set_label("正在执行 read_file")

    assert spinner._label == "正在执行 read_file"
