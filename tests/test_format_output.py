"""Tests for JSON-aware command output formatting."""

from testbed.format_output import (
    format_argv_for_display,
    format_command_output,
    format_command_output_one_line,
    parse_command_output_as_json,
)


def test_format_command_output_plain_one_line() -> None:
    """Non-JSON output is first line, truncated when long."""
    out = format_command_output_one_line("hello\nworld", "", verbose=False, max_len=20)
    assert out == "hello"
    out = format_command_output_one_line("a" * 100, "", verbose=False, max_len=10)
    assert out == "a" * 10 + "..."


def test_format_command_output_json_pretty_list() -> None:
    """Valid JSON list yields pretty-printed output (verbose and non-verbose)."""
    out = format_command_output_one_line("[1,2,3]", "", verbose=False)
    assert "1" in out and "2" in out and "3" in out
    assert "\n" in out or out == "[1, 2, 3]"  # indent=2 adds newlines
    out_empty = format_command_output_one_line("[]", "", verbose=False)
    assert out_empty == "[]"


def test_format_command_output_json_pretty_dict_assets() -> None:
    """JSON object with 'assets' key yields pretty-printed output."""
    out = format_command_output_one_line(
        '{"assets": [{"id": 1}, {"id": 2}]}', "", verbose=False
    )
    assert "assets" in out
    assert "id" in out
    assert "\n" in out


def test_format_command_output_verbose_pretty_json() -> None:
    """Verbose mode pretty-prints JSON."""
    raw = '{"a":1,"b":2}'
    out = format_command_output(raw, "", verbose=True)
    assert '  "a": 1' in out or '"a": 1' in out
    assert '  "b": 2' in out or '"b": 2' in out


def test_format_command_output_non_verbose_also_pretty_json() -> None:
    """Non-verbose mode also pretty-prints JSON (same as verbose for JSON)."""
    raw = '{"a":1,"b":2}'
    out = format_command_output(raw, "", verbose=False)
    assert '  "a": 1' in out or '"a": 1' in out
    assert '  "b": 2' in out or '"b": 2' in out


def test_format_command_output_verbose_plain_unchanged() -> None:
    """Verbose mode leaves non-JSON as-is (first line)."""
    out = format_command_output("not json\nline2", "", verbose=True, max_len=50)
    assert out == "not json"


def test_parse_command_output_as_json_valid() -> None:
    """parse_command_output_as_json returns (True, pretty_str) for valid JSON."""
    is_json, out = parse_command_output_as_json('{"a":1}\n', "")
    assert is_json is True
    assert "a" in out and "1" in out
    is_json2, out2 = parse_command_output_as_json("", "[1,2,3]")
    assert is_json2 is True
    assert "1" in out2 and "2" in out2


def test_parse_command_output_as_json_invalid() -> None:
    """parse_command_output_as_json returns (False, summary) for non-JSON."""
    is_json, out = parse_command_output_as_json("hello\nworld", "")
    assert is_json is False
    assert out == "hello"
    is_json2, out2 = parse_command_output_as_json("", "")
    assert is_json2 is False
    assert out2 == "(no output)"


def test_format_argv_for_display_empty() -> None:
    """Empty argv renders as empty string."""
    assert format_argv_for_display([]) == ""


def test_format_argv_for_display_shell_safe_join() -> None:
    """Args containing spaces are shell-escaped for display clarity."""
    rendered = format_argv_for_display(["curl", "-H", "X Name: demo value", "http://api/"])
    assert rendered.startswith("curl -H ")
    assert "'X Name: demo value'" in rendered
    assert rendered.endswith("http://api/")
