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
