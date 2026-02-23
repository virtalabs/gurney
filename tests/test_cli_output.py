"""CLI output tests: default one-line output; verbose layered output."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from testbed.cli import app
from testbed.runner import RunResult

runner = CliRunner()


def test_run_default_produces_single_line_output_when_no_commands() -> None:
    """Without --verbose and no up nodes/commands, run produces only PASS line."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=[],
            command_outputs=[],
        )
        result = runner.invoke(app, ["run", "smoke-minimal"])
    assert m.called
    assert result.exit_code == 0
    assert "PASS smoke-minimal (1.0s)" in result.stdout


def test_run_default_produces_up_cmd_pass_structure() -> None:
    """Without --verbose, run produces up lines, cmd blocks, then PASS."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=2.5,
            up_node_ids=["postgres", "api"],
            command_outputs=[
                ("verify", ["http_check", "http://api:8000/"], "[]\n200", ""),
            ],
        )
        result = runner.invoke(app, ["run", "smoke-minimal"])
    assert result.exit_code == 0
    out = result.stdout
    assert "  up postgres" in out
    assert "  up api" in out
    assert "  cmd verify" in out
    assert "http_check http://api:8000/" in out
    assert "PASS smoke-minimal (2.5s)" in out


def test_run_verbose_produces_layer_headers() -> None:
    """With --verbose and success, output contains Docker Compose, Container, Testbed sections."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "tapirx-dicom-discovery").exists():
        pytest.skip("tapirx-dicom-discovery scenario not found")

    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="tapirx-dicom-discovery",
            passed=True,
            duration_s=3.5,
            compose_up_stdout="[+] Running 2/2\n ✔ Network ...",
            compose_up_stderr="",
            command_outputs=[
                ("replay-dicom", ["tcpreplay", "-i", "eth0"], "replay stdout\n", ""),
                ("verify-assets", ["http_check", "http://api/assets/"], '{"id":"ECHOSCU"}\n', " Container build-mock-asset-api-1  Running"),
            ],
            output_files=None,
        )
        result = runner.invoke(app, ["run", "tapirx-dicom-discovery", "--verbose"])
    assert result.exit_code == 0
    out = result.stdout
    assert "------ Docker Compose (up) ------" in out
    assert "------ Command replay-dicom ------" in out
    assert "------ Command verify-assets ------" in out
    assert "------ Testbed ------" in out
    assert "PASS tapirx-dicom-discovery (3.5s)" in out
    # Container section shows only container stdout; compose daemon stderr is omitted
    assert "build-mock-asset-api-1" not in out


def test_run_verbose_includes_scenario_output_section() -> None:
    """With --verbose and output_files, Scenario output section is printed."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")

    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            compose_up_stdout="",
            compose_up_stderr="",
            command_outputs=[],
            output_files=[("assets.jsonl", '{"x":1}\n'), ("log.txt", "ok\n")],
        )
        result = runner.invoke(app, ["run", "smoke-minimal", "--verbose"])
    assert result.exit_code == 0
    out = result.stdout
    assert "------ Scenario output ------" in out
    assert "--- assets.jsonl ---" in out
    assert '{"x":1}' in out
    assert "--- log.txt ---" in out
    assert "ok" in out


def test_run_failure_default_output_structure() -> None:
    """On failure, default output shows up/cmd up to failure, then FAIL and error on stderr."""
    root = Path(__file__).resolve().parent.parent
    scenarios_dir = root / "scenarios"
    if not (scenarios_dir / "tapirx-dicom-discovery").exists():
        pytest.skip("tapirx-dicom-discovery scenario not found")

    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="tapirx-dicom-discovery",
            passed=False,
            duration_s=0.5,
            error="Command 'replay-dicom' failed after 1 attempt(s): pcap not found",
            up_node_ids=["postgres", "api"],
            command_outputs=[
                ("verify-assets", ["http_check", "http://api/"], "[]\n200", ""),
                ("replay-dicom", ["tcpreplay", "-i", "eth0", "/pcap/file.pcap"], "", "pcap not found"),
            ],
        )
        result = runner.invoke(app, ["run", "tapirx-dicom-discovery"])
    assert result.exit_code != 0
    assert "  up postgres" in result.stdout
    assert "  up api" in result.stdout
    assert "  cmd verify-assets" in result.stdout
    assert "  cmd replay-dicom" in result.stdout
    assert "FAIL tapirx-dicom-discovery" in result.stderr
    assert "Command 'replay-dicom' failed" in result.stderr


def test_run_ui_classic_explicit_same_as_default() -> None:
    """--ui classic produces the same structure as default (no --ui)."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=["api"],
            command_outputs=[],
        )
        result = runner.invoke(app, ["run", "smoke-minimal", "--ui", "classic"])
    assert result.exit_code == 0
    assert "  up api" in result.stdout
    assert "PASS smoke-minimal (1.0s)" in result.stdout


def test_run_default_json_command_output_prettified() -> None:
    """Default output shows prettified JSON for command stdout that is JSON."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=[],
            command_outputs=[
                ("get-assets", ["curl", "-s", "http://api/assets"], '{"assets":[{"id":1}]}\n', ""),
            ],
        )
        result = runner.invoke(app, ["run", "smoke-minimal"])
    assert result.exit_code == 0
    assert "  cmd get-assets" in result.stdout
    assert "assets" in result.stdout and "id" in result.stdout
    assert "\n" in result.stdout  # prettified JSON has newlines


def test_run_verbose_json_command_output_expanded() -> None:
    """Verbose output pretty-prints JSON command stdout."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            compose_up_stdout="",
            compose_up_stderr="",
            command_outputs=[
                ("get-assets", ["curl", "http://api/assets"], '{"assets":[{"id":1}]}\n', ""),
            ],
            output_files=None,
        )
        result = runner.invoke(app, ["run", "smoke-minimal", "--verbose"])
    assert result.exit_code == 0
    assert "------ Command get-assets ------" in result.stdout
    # Pretty-printed JSON has newlines/indent
    assert "assets" in result.stdout and ("[" in result.stdout or '"id"' in result.stdout)


def test_run_no_color_omits_ansi_in_pass_line() -> None:
    """With --no-color, PASS line does not contain ANSI escape codes."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=[],
            command_outputs=[],
        )
        result = runner.invoke(app, ["run", "smoke-minimal", "--no-color"])
    assert result.exit_code == 0
    assert "PASS smoke-minimal (1.0s)" in result.stdout
    assert "\033[" not in result.stdout


def test_run_default_command_line_has_dollar_prompt() -> None:
    """Default output shows '$ argv' so JSON panel can be nested under it."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=[],
            command_outputs=[
                ("check", ["http_check", "http://api/health"], "ok", ""),
            ],
        )
        result = runner.invoke(app, ["run", "smoke-minimal"])
    assert result.exit_code == 0
    assert "     $ http_check http://api/health" in result.stdout
    assert "  cmd check" in result.stdout


def test_run_no_color_json_plain_text_no_ansi() -> None:
    """With --no-color, JSON command output is plain prettified text with no ANSI."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=[],
            command_outputs=[
                ("get", ["curl", "http://api/"], '{"status":"ok"}\n', ""),
            ],
        )
        result = runner.invoke(app, ["run", "smoke-minimal", "--no-color"])
    assert result.exit_code == 0
    assert "status" in result.stdout and "ok" in result.stdout
    assert "\033[" not in result.stdout
