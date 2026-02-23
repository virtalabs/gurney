"""Format command output for display: JSON prettified (jq-style) or one-line fallback."""

import json


def parse_command_output_as_json(stdout: str, stderr: str) -> tuple[bool, str]:
    """If command output is valid JSON, return (True, pretty-printed string). Else (False, summary line or empty)."""
    raw = (stdout or "").strip()
    if not raw:
        raw = (stderr or "").strip()
    if not raw:
        return (False, "(no output)")
    try:
        data = json.loads(raw)
        return (True, json.dumps(data, indent=2))
    except (json.JSONDecodeError, ValueError):
        line = raw.split("\n")[0]
        summary = (line[:120] + "...") if len(line) > 120 else line
        return (False, summary or "(no output)")


def format_command_output(
    stdout: str,
    stderr: str,
    verbose: bool,
    max_len: int = 120,
) -> str:
    """Format command stdout/stderr for display. If valid JSON: always pretty-print (indent=2). Otherwise first line truncated to max_len."""
    raw = (stdout or "").strip()
    if not raw:
        raw = (stderr or "").strip()
    if not raw:
        return "(no output)"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Not JSON: use first line, truncated
        line = raw.split("\n")[0]
        return (line[:max_len] + "...") if len(line) > max_len else line
    return json.dumps(data, indent=2)


def format_command_output_one_line(
    stdout: str,
    stderr: str,
    verbose: bool,
    max_len: int = 120,
) -> str:
    """Format command output: if valid JSON return pretty-printed (multi-line); else first line truncated. Used for default cmd summary and live UI."""
    raw = (stdout or "").strip()
    if not raw:
        raw = (stderr or "").strip()
    if not raw:
        return "(no output)"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        line = raw.split("\n")[0]
        return (line[:max_len] + "...") if len(line) > max_len else line
    return json.dumps(data, indent=2)
