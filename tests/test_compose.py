"""Tests for compose lifecycle (compose_up, compose_run return values)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from testbed.compose import (
    _compose_ps_snapshot,
    _run_with_optional_stream,
    compose_logs,
    compose_run,
    compose_up,
)


def test_compose_up_returns_stdout_stderr(tmp_path: Path) -> None:
    """compose_up always captures and returns (stdout, stderr)."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}")
    with patch("testbed.compose.subprocess.run") as m:
        m.return_value = type("R", (), {"returncode": 0, "stdout": "out", "stderr": "err"})()
        out, err = compose_up(compose_path)
    assert out == "out"
    assert err == "err"
    m.assert_called_once()
    assert m.call_args.kwargs["capture_output"] is True


def test_compose_run_returns_stdout_stderr(tmp_path: Path) -> None:
    """compose_run always captures and returns (stdout, stderr); no print."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}")
    with patch("testbed.compose.subprocess.run") as m:
        m.return_value = type("R", (), {"returncode": 0, "stdout": "cout", "stderr": "cerr"})()
        out, err = compose_run(compose_path, "svc")
    assert out == "cout"
    assert err == "cerr"
    m.assert_called_once()
    assert m.call_args.kwargs["capture_output"] is True


def test_compose_up_raises_on_nonzero() -> None:
    """compose_up raises RuntimeError on non-zero returncode."""
    with patch("testbed.compose.subprocess.run") as m:
        m.return_value = type(
            "R", (), {"returncode": 1, "stdout": "o", "stderr": "e"}
        )()
        with pytest.raises(RuntimeError) as exc_info:
            compose_up(Path("/nonexistent/compose.yaml"))
    assert "docker compose up failed" in str(exc_info.value)


def test_compose_run_raises_on_nonzero() -> None:
    """compose_run raises RuntimeError on non-zero returncode."""
    with patch("testbed.compose.subprocess.run") as m:
        m.return_value = type(
            "R", (), {"returncode": 1, "stdout": "o", "stderr": "e"}
        )()
        with pytest.raises(RuntimeError) as exc_info:
            compose_run(Path("/nonexistent/compose.yaml"), "svc")
    assert "docker compose run svc failed" in str(exc_info.value)


def test_compose_run_with_command_includes_entrypoint_empty(tmp_path: Path) -> None:
    """compose_run with command adds --entrypoint '' before service and appends full command."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}")
    with patch("testbed.compose.subprocess.run") as m:
        m.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        compose_run(
            compose_path,
            "replay",
            command=["tcpreplay", "-i", "eth0", "/pcap/file.pcap"],
        )
    m.assert_called_once()
    cmd = m.call_args.args[0]
    # --entrypoint '' must appear before service name
    idx_entrypoint = cmd.index("--entrypoint")
    idx_service = cmd.index("replay")
    assert cmd[idx_entrypoint] == "--entrypoint"
    assert cmd[idx_entrypoint + 1] == ""
    assert idx_entrypoint < idx_service
    # Command args appear after service name
    assert cmd[idx_service + 1 :] == ["tcpreplay", "-i", "eth0", "/pcap/file.pcap"]


def test_compose_logs_returns_per_service_logs(tmp_path: Path) -> None:
    """compose_logs runs logs for each service and returns [(service, logs), ...]."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}")
    with patch("testbed.compose.subprocess.run") as m:
        m.side_effect = [
            type("R", (), {"returncode": 0, "stdout": "log1", "stderr": ""})(),
            type("R", (), {"returncode": 0, "stdout": "log2", "stderr": ""})(),
        ]
        result = compose_logs(compose_path, ["s1", "s2"])
    assert result == [("s1", "log1"), ("s2", "log2")]
    assert m.call_count == 2
    assert m.call_args_list[0].args[0][-1] == "s1"
    assert m.call_args_list[1].args[0][-1] == "s2"


def test_compose_run_with_on_line_calls_run_with_optional_stream(tmp_path: Path) -> None:
    """When on_line is provided, compose_run uses _run_with_optional_stream with that handler."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}")
    lines_seen: list[tuple[str, str]] = []

    def on_line(line: str, stream: str) -> None:
        lines_seen.append((line, stream))

    with patch("testbed.compose._run_with_optional_stream") as m:
        m.return_value = ("out", "err", 0)
        compose_run(compose_path, "svc", on_line=on_line)
    m.assert_called_once()
    assert m.call_args.args[1] is on_line


def test_compose_run_nonzero_returncode_22_appends_http_hint(tmp_path: Path) -> None:
    """compose_run adds curl HTTP hint when returncode is 22."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}")
    with patch("testbed.compose._run_with_optional_stream") as m:
        m.return_value = ("out-body", "err-body", 22)
        with pytest.raises(RuntimeError) as exc_info:
            compose_run(compose_path, "check-0")
    msg = str(exc_info.value)
    assert "docker compose run check-0 failed" in msg
    assert "Curl exit 22 = HTTP 4xx/5xx" in msg


def test_compose_ps_snapshot_parses_json_array_output(tmp_path: Path) -> None:
    """_compose_ps_snapshot parses array JSON output from docker compose ps."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}")
    payload = (
        '[{"Service":"api","State":"running","Health":"healthy","ExitCode":0},'
        '{"Service":"worker","State":"exited","Health":"","ExitCode":1}]'
    )
    with patch("testbed.compose.subprocess.run") as m:
        m.return_value = type("R", (), {"returncode": 0, "stdout": payload, "stderr": ""})()
        snapshot = _compose_ps_snapshot(compose_path)
    assert snapshot["services"] == [
        {"service": "api", "state": "running", "health": "healthy", "exit_code": 0},
        {"service": "worker", "state": "exited", "health": "", "exit_code": 1},
    ]
