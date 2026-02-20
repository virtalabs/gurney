"""CLI output tests: default one-line output; verbose layered output."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from testbed.cli import app
from testbed.runner import RunResult

runner = CliRunner()


def test_run_default_produces_single_line_output() -> None:
    """Without --verbose, run produces only one line (PASS or FAIL)."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "scenarios" / "smoke-minimal").exists():
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
        )
        result = runner.invoke(app, ["run", "smoke-minimal"])
    assert m.called
    assert result.exit_code == 0
    lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
    assert len(lines) == 1, f"Expected 1 line, got: {result.stdout!r}"
    assert lines[0].startswith("PASS smoke-minimal")


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
            run_once_outputs=[
                ("replay", "replay stdout\n", ""),
                ("tapirx-pcap", '{"id":"ECHOSCU"}\n', " Container build-mock-asset-api-1  Running"),
            ],
            output_files=None,
        )
        result = runner.invoke(app, ["run", "tapirx-dicom-discovery", "--verbose"])
    assert result.exit_code == 0
    out = result.stdout
    assert "------ Docker Compose (up) ------" in out
    assert "------ Container replay ------" in out
    assert "------ Container tapirx-pcap ------" in out
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
            run_once_outputs=None,
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


def test_run_failure_no_verbose_block() -> None:
    """On failure, no layered block is printed even if verbose was requested."""
    root = Path(__file__).resolve().parent.parent
    scenarios_dir = root / "scenarios"
    if not (scenarios_dir / "tapirx-dicom-discovery").exists():
        pytest.skip("tapirx-dicom-discovery scenario not found")

    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="tapirx-dicom-discovery",
            passed=False,
            duration_s=0.5,
            error="compose up failed",
        )
        result = runner.invoke(
            app,
            ["run", "tapirx-dicom-discovery", "--verbose"],
            env={},
        )
    assert result.exit_code != 0
    assert "------ Docker Compose (up) ------" not in result.stdout
    assert "FAIL tapirx-dicom-discovery" in result.stderr or "FAIL" in result.stdout
    assert "compose up failed" in result.stderr or "compose up failed" in result.stdout
