"""CLI output tests: default one-line output; verbose layered output."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from testbed.cli import app
from testbed.runner import RunResult

runner = CliRunner()
SMOKE_MINIMAL_REF = "minimal-thirdparty/smoke-minimal"
TAPIRX_DISCOVERY_REF = "blueflow-local/tapirx-dicom-discovery"


def _scenario_dir_exists(root: Path, scenario_ref: str) -> bool:
    topology_id, scenario_id = scenario_ref.split("/", 1)
    return (root / "topologies" / topology_id / "scenarios" / scenario_id).exists()


def test_run_default_produces_single_line_output_when_no_commands() -> None:
    """Without --verbose and no up nodes/commands, run produces only PASS line."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=[],
            command_outputs=[],
        )
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF])
    assert m.called
    assert result.exit_code == 0
    assert "PASS smoke-minimal (1.0s)" in result.stdout


def test_run_default_produces_up_cmd_pass_structure() -> None:
    """Without --verbose, run produces up lines, cmd blocks, then PASS."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
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
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF])
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
    if not _scenario_dir_exists(root, TAPIRX_DISCOVERY_REF):
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
        result = runner.invoke(app, ["run", TAPIRX_DISCOVERY_REF, "--verbose"])
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
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
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
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF, "--verbose"])
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
    if not _scenario_dir_exists(root, TAPIRX_DISCOVERY_REF):
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
        result = runner.invoke(app, ["run", TAPIRX_DISCOVERY_REF])
    assert result.exit_code != 0
    assert "  up postgres" in result.stdout
    assert "  up api" in result.stdout
    assert "  cmd verify-assets" in result.stdout
    assert "  cmd replay-dicom" in result.stdout
    assert "FAIL tapirx-dicom-discovery" in result.stderr
    assert "Command 'replay-dicom' failed" in result.stderr


def test_run_rejects_removed_ui_flag() -> None:
    """`--ui` is removed in favor of root-level `--json`."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
        pytest.skip("smoke-minimal scenario not found")
    result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF, "--ui", "classic"])
    assert result.exit_code != 0
    assert "No such option: --ui" in result.stderr


def test_run_default_json_command_output_prettified() -> None:
    """Default output shows prettified JSON for command stdout that is JSON."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
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
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF])
    assert result.exit_code == 0
    assert "  cmd get-assets" in result.stdout
    assert "assets" in result.stdout and "id" in result.stdout
    assert "\n" in result.stdout  # prettified JSON has newlines


def test_run_verbose_json_command_output_expanded() -> None:
    """Verbose output pretty-prints JSON command stdout."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
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
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF, "--verbose"])
    assert result.exit_code == 0
    assert "------ Command get-assets ------" in result.stdout
    # Pretty-printed JSON has newlines/indent
    assert "assets" in result.stdout and ("[" in result.stdout or '"id"' in result.stdout)


def test_run_no_color_omits_ansi_in_pass_line() -> None:
    """With --no-color, PASS line does not contain ANSI escape codes."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
        pytest.skip("smoke-minimal scenario not found")
    with patch("testbed.cli.run_scenario") as m:
        m.return_value = RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=1.0,
            up_node_ids=[],
            command_outputs=[],
        )
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF, "--no-color"])
    assert result.exit_code == 0
    assert "PASS smoke-minimal (1.0s)" in result.stdout
    assert "\033[" not in result.stdout


def test_run_default_command_line_has_dollar_prompt() -> None:
    """Default output shows '$ argv' so JSON panel can be nested under it."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
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
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF])
    assert result.exit_code == 0
    assert "     $ http_check http://api/health" in result.stdout
    assert "  cmd check" in result.stdout


def test_run_no_color_json_plain_text_no_ansi() -> None:
    """With --no-color, JSON command output is plain prettified text with no ANSI."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
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
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF, "--no-color"])
    assert result.exit_code == 0
    assert "status" in result.stdout and "ok" in result.stdout
    assert "\033[" not in result.stdout


def test_pull_with_topology_id_calls_reproduce_for_that_topology() -> None:
    """`testbed pull <topology-id>` resolves topology directly."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "topologies" / "blueflow-local" / "topology.yaml").exists():
        pytest.skip("blueflow-local topology not found")
    with patch("testbed.cli.pull_and_verify") as m:
        result = runner.invoke(app, ["pull", "blueflow-local"])
    assert result.exit_code == 0
    m.assert_called_once_with("blueflow-local")


def test_pull_with_scenario_ref_resolves_topology_id() -> None:
    """`testbed pull <topology>/<scenario>` resolves topology from index."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, TAPIRX_DISCOVERY_REF):
        pytest.skip("tapirx-dicom-discovery scenario not found")
    with patch("testbed.cli.pull_and_verify") as m:
        result = runner.invoke(app, ["pull", TAPIRX_DISCOVERY_REF])
    assert result.exit_code == 0
    m.assert_called_once_with("blueflow-local")


def test_run_json_emits_ndjson_envelope_and_terminal_event() -> None:
    """`testbed --json run` emits NDJSON started/event/completed lines."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
        pytest.skip("smoke-minimal scenario not found")

    def _fake_run(*_args, **kwargs):
        handler = kwargs["event_handler"]
        handler(SimpleNamespace(kind="command_started", payload={"command_id": "check"}))
        return RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=0.4,
            up_node_ids=[],
            command_outputs=[],
        )

    with patch("testbed.cli.run_scenario", side_effect=_fake_run):
        result = runner.invoke(app, ["--json", "run", SMOKE_MINIMAL_REF])

    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events[0]["command"] == "run"
    assert events[0]["event"] == "started"
    assert events[1]["event"] == "command_started"
    assert events[-1]["event"] == "completed"
    assert events[-1]["payload"]["passed"] is True


def test_list_json_emits_started_topology_completed() -> None:
    """`testbed --json list` emits a consistent NDJSON event sequence."""
    result = runner.invoke(app, ["--json", "list"])
    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events[0]["command"] == "list"
    assert events[0]["event"] == "started"
    assert events[-1]["event"] == "completed"
    assert "scenario_count" in events[-1]["payload"]


def test_list_appended_json_emits_started_topology_completed() -> None:
    """`testbed list --json` emits the same NDJSON sequence."""
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events[0]["command"] == "list"
    assert events[0]["event"] == "started"
    assert events[-1]["event"] == "completed"
    assert "scenario_count" in events[-1]["payload"]


def test_pull_json_emits_progress_and_terminal_events() -> None:
    """`testbed --json pull` forwards reproduce progress events as NDJSON."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "topologies" / "blueflow-local" / "topology.yaml").exists():
        pytest.skip("blueflow-local topology not found")

    def _fake_pull(topology_id, reporter=None, emit_text=True):
        assert topology_id == "blueflow-local"
        assert reporter is not None
        assert emit_text is False
        reporter("config_loaded", {"topology_id": topology_id})

    with patch("testbed.cli.pull_and_verify", side_effect=_fake_pull):
        result = runner.invoke(app, ["--json", "pull", "blueflow-local"])

    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events[0]["event"] == "started"
    assert any(event["event"] == "config_loaded" for event in events)
    assert events[-1]["event"] == "completed"


def test_pull_appended_json_emits_progress_and_terminal_events() -> None:
    """`testbed pull <target> --json` forwards progress as NDJSON."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "topologies" / "blueflow-local" / "topology.yaml").exists():
        pytest.skip("blueflow-local topology not found")

    def _fake_pull(topology_id, reporter=None, emit_text=True):
        assert topology_id == "blueflow-local"
        assert reporter is not None
        assert emit_text is False
        reporter("config_loaded", {"topology_id": topology_id})

    with patch("testbed.cli.pull_and_verify", side_effect=_fake_pull):
        result = runner.invoke(app, ["pull", "blueflow-local", "--json"])

    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events[0]["event"] == "started"
    assert any(event["event"] == "config_loaded" for event in events)
    assert events[-1]["event"] == "completed"


def test_teardown_json_emits_started_completed() -> None:
    """`testbed --json teardown` emits lifecycle NDJSON events."""
    with patch("testbed.cli.force_cleanup"):
        result = runner.invoke(app, ["--json", "teardown"])
    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert [event["event"] for event in events] == ["started", "completed"]


def test_teardown_appended_json_emits_started_completed() -> None:
    """`testbed teardown --json` emits lifecycle NDJSON events."""
    with patch("testbed.cli.force_cleanup"):
        result = runner.invoke(app, ["teardown", "--json"])
    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert [event["event"] for event in events] == ["started", "completed"]


def test_run_appended_json_emits_ndjson_envelope_and_terminal_event() -> None:
    """`testbed run <scenario> --json` emits NDJSON started/event/completed lines."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
        pytest.skip("smoke-minimal scenario not found")

    def _fake_run(*_args, **kwargs):
        handler = kwargs["event_handler"]
        handler(SimpleNamespace(kind="command_started", payload={"command_id": "check"}))
        return RunResult(
            scenario="smoke-minimal",
            passed=True,
            duration_s=0.4,
            up_node_ids=[],
            command_outputs=[],
        )

    with patch("testbed.cli.run_scenario", side_effect=_fake_run):
        result = runner.invoke(app, ["run", SMOKE_MINIMAL_REF, "--json"])

    assert result.exit_code == 0
    events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert events[0]["command"] == "run"
    assert events[0]["event"] == "started"
    assert events[1]["event"] == "command_started"
    assert events[-1]["event"] == "completed"
    assert events[-1]["payload"]["passed"] is True


def test_duplicate_json_flag_fails_for_list() -> None:
    """Passing root and appended --json together should fail clearly."""
    result = runner.invoke(app, ["--json", "list", "--json"])
    assert result.exit_code != 0
    assert "Do not pass --json twice" in result.stderr


def test_duplicate_json_flag_fails_for_run() -> None:
    """Duplicate --json should fail before scenario execution."""
    root = Path(__file__).resolve().parent.parent
    if not _scenario_dir_exists(root, SMOKE_MINIMAL_REF):
        pytest.skip("smoke-minimal scenario not found")
    result = runner.invoke(app, ["--json", "run", SMOKE_MINIMAL_REF, "--json"])
    assert result.exit_code != 0
    assert "Do not pass --json twice" in result.stderr
