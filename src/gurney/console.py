"""Console capabilities: TTY detection and color policy."""

import os
import sys
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


def use_live_ui() -> bool:
    """Return True when live UI rendering should be used."""
    return is_tty()
