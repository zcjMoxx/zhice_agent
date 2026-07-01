"""Tiny console styling helpers for CLI output."""

from __future__ import annotations

import os
import sys
import threading
import time

_COLOR_ENABLED: bool | None = None
_COLORAMA_FIXED = False


class Console:
    """Small style facade shared by CLI and future local runtime logs."""

    def bold(self, text: object) -> str:
        """Return bold text when terminal color is available."""

        return _style(text, "1")

    def error(self, text: object) -> str:
        """Return error-colored text when terminal color is available."""

        return _style(text, "31")

    def success(self, text: object) -> str:
        """Return success-colored text when terminal color is available."""

        return _style(text, "32")

    def warning(self, text: object) -> str:
        """Return warning-colored text when terminal color is available."""

        return _style(text, "33")

    def command(self, text: object) -> str:
        """Return command-colored text when terminal color is available."""

        return _style(text, "36")

    def path(self, text: object) -> str:
        """Return path-colored text when terminal color is available."""

        return _style(text, "36")


console = Console()


def _style(text: object, code: str) -> str:
    """Wrap text in one ANSI style code when color output is enabled."""

    if not _colors_enabled():
        return str(text)
    return f"\033[{code}m{text}\033[0m"


def _colors_enabled() -> bool:
    """Cache whether this process should emit ANSI color sequences."""

    global _COLOR_ENABLED
    if _COLOR_ENABLED is not None:
        return _COLOR_ENABLED
    _COLOR_ENABLED = _detect_color_support()
    return _COLOR_ENABLED


def _detect_color_support() -> bool:
    """Detect color support across non-Windows and Windows terminals."""

    if os.getenv("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    if _fix_windows_console_with_colorama():
        return True
    return _enable_windows_virtual_terminal()


def _fix_windows_console_with_colorama() -> bool:
    """Let colorama enable ANSI handling on Windows when it is installed."""

    global _COLORAMA_FIXED
    if _COLORAMA_FIXED:
        return True
    try:
        from colorama import just_fix_windows_console
    except ImportError:
        return False
    just_fix_windows_console()
    _COLORAMA_FIXED = True
    return True


def _enable_windows_virtual_terminal() -> bool:
    """Enable native Windows virtual terminal processing for ANSI colors."""

    try:
        import ctypes
    except ImportError:
        return False

    kernel32 = ctypes.windll.kernel32
    stdout_handle = kernel32.GetStdHandle(-11)
    if stdout_handle in (0, -1):
        return False

    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(stdout_handle, ctypes.byref(mode)):
        return False

    enable_virtual_terminal_processing = 0x0004
    new_mode = mode.value | enable_virtual_terminal_processing
    return bool(kernel32.SetConsoleMode(stdout_handle, new_mode))


class Spinner:
    """Animated spinner with elapsed time, shown while waiting for LLM."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _INTERVAL = 0.08

    def __init__(self, label: str = "thinking"):
        """Prepare spinner state without starting the background thread yet."""

        self._label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start: float = 0.0
        self._last_width = 0
        self.elapsed: float = 0.0
        self.interrupted: bool = False

    def __enter__(self) -> Spinner:
        """Start the spinner thread when stderr is an interactive terminal."""

        self._start = time.monotonic()
        self._last_width = 0
        if not sys.stderr.isatty():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: type | None, exc_val: object, exc_tb: object) -> None:
        """Stop the spinner and print the final elapsed-time line."""

        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self.elapsed = time.monotonic() - self._start if self._start else 0.0
        self.interrupted = exc_type is KeyboardInterrupt
        if sys.stderr.isatty():
            visible_line = f"⠿ {self._label} {self.elapsed:.1f}s"
            if self.interrupted:
                tag = " [interrupted]"
                visible_line += tag
                if _colors_enabled():
                    tag = " \033[33m[interrupted]\033[0m"
                self._write_status(f"⠿ {self._label} {self.elapsed:.1f}s{tag}", len(visible_line))
            else:
                self._write_status(visible_line, len(visible_line))
            sys.stderr.write("\n")
            sys.stderr.flush()

    def _spin(self) -> None:
        """Refresh the terminal spinner until the context manager exits."""

        index = 0
        use_color = _colors_enabled()
        while not self._stop.is_set():
            frame = self._FRAMES[index % len(self._FRAMES)]
            elapsed = time.monotonic() - self._start
            visible_line = f"{frame} {self._label}... {elapsed:.1f}s"
            if use_color:
                line = f"\033[36m{frame}\033[0m {self._label}... {elapsed:.1f}s"
            else:
                line = visible_line
            self._write_status(line, len(visible_line))
            index += 1
            self._stop.wait(self._INTERVAL)

    def _write_status(self, text: str, visible_width: int) -> None:
        """Rewrite one terminal status line and erase leftovers from longer frames."""

        padding = " " * max(0, self._last_width - visible_width)
        sys.stderr.write(f"\r{text}{padding}")
        sys.stderr.flush()
        self._last_width = visible_width
