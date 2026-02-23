"""Console capabilities: TTY detection, color policy, and semantic style helpers."""

import os
import sys
from typing import Literal

UIMode = Literal["classic", "live"]


def is_tty(stream: object = None) -> bool:
    """Return True if stream is an interactive terminal. Default: stdout."""
    if stream is None:
        stream = sys.stdout
    return getattr(stream, "isatty", lambda: False)()


def should_use_color(no_color_flag: bool = False) -> bool:
    """Return True if output should use ANSI color. Respects NO_COLOR and --no-color."""
    if no_color_flag:
        return False
    if os.environ.get("NO_COLOR", "").strip().lower() in ("1", "true", "yes"):
        return False
    return is_tty()


def use_live_ui(ui_mode: UIMode) -> bool:
    """Return True if live UI (banner, spinner, streamed output) should be used."""
    return ui_mode == "live" and is_tty()
