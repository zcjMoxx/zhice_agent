"""Tiny console styling helpers for CLI output."""

from __future__ import annotations

import os
import sys

_COLOR_ENABLED: bool | None = None
_COLORAMA_FIXED = False


class Console:
    """Small style facade shared by CLI and future local runtime logs."""

    def bold(self, text: object) -> str:
        return _style(text, "1")

    def error(self, text: object) -> str:
        return _style(text, "31")

    def success(self, text: object) -> str:
        return _style(text, "32")

    def warning(self, text: object) -> str:
        return _style(text, "33")

    def command(self, text: object) -> str:
        return _style(text, "36")

    def path(self, text: object) -> str:
        return _style(text, "36")


console = Console()


def _style(text: object, code: str) -> str:
    if not _colors_enabled():
        return str(text)
    return f"\033[{code}m{text}\033[0m"


def _colors_enabled() -> bool:
    global _COLOR_ENABLED
    if _COLOR_ENABLED is not None:
        return _COLOR_ENABLED
    _COLOR_ENABLED = _detect_color_support()
    return _COLOR_ENABLED


def _detect_color_support() -> bool:
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
