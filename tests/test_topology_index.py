"""Tests for lazy co-located topology/scenario index."""

from pathlib import Path
import time
from unittest.mock import patch

from typer.testing import CliRunner

from testbed.cli import app
from testbed.runner import RunResult
from testbed.topology_index import ScenarioIndexEntry, get_or_build_index

runner = CliRunner()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_fixture_tree(root: Path) -> None:
    _write(root / "topologies/alpha/topology.yaml", "networks: []\nnodes: []\n")
    _write(
        root / "topologies/alpha/scenarios/smoke/scenario.yaml",
        "name: smoke\ntopology: topologies/alpha/topology.yaml\nnodes: []\n",
    )
    _write(root / "topologies/beta/topology.yaml", "networks: []\nnodes: []\n")
    _write(
        root / "topologies/beta/scenarios/discovery/scenario.yaml",
        "name: discovery\ntopology: topologies/beta/topology.yaml\nnodes: []\n",
    )


def test_get_or_build_index_regeneration_lifecycle(tmp_path: Path) -> None:
    """Index regenerates on relevant changes and reuses cache otherwise."""
    _build_fixture_tree(tmp_path)

    _, regenerated = get_or_build_index(tmp_path)
    assert regenerated is True

    index_path = tmp_path / "var/index/topologies.index.yaml"
    first_mtime = index_path.stat().st_mtime_ns

    _, regenerated = get_or_build_index(tmp_path)
    assert regenerated is False
    assert index_path.stat().st_mtime_ns == first_mtime

    # Unrelated file changes must not trigger regeneration.
    _write(tmp_path / "README.tmp", "ignore me\n")
    _, regenerated = get_or_build_index(tmp_path)
    assert regenerated is False
    assert index_path.stat().st_mtime_ns == first_mtime

    # Scenario changes must trigger regeneration.
    time.sleep(0.002)
    _write(
        tmp_path / "topologies/alpha/scenarios/smoke/scenario.yaml",
        "name: smoke\ntopology: topologies/alpha/topology.yaml\nnodes: []\ncommands: []\n",
    )
    _, regenerated = get_or_build_index(tmp_path)
    assert regenerated is True
    second_mtime = index_path.stat().st_mtime_ns
    assert second_mtime > first_mtime

    # Topology changes must trigger regeneration.
    time.sleep(0.002)
    _write(tmp_path / "topologies/beta/topology.yaml", "networks: []\nnodes: []\n# changed\n")
    _, regenerated = get_or_build_index(tmp_path)
    assert regenerated is True
    assert index_path.stat().st_mtime_ns > second_mtime


def test_get_or_build_index_raises_on_duplicate_scenario_ref(tmp_path: Path) -> None:
    """Duplicate scenario_ref values fail index regeneration."""
    _build_fixture_tree(tmp_path)

    dup_entries = [
        ScenarioIndexEntry(
            id="smoke",
            ref="shared/smoke",
            path="topologies/alpha/scenarios/smoke/scenario.yaml",
        ),
        ScenarioIndexEntry(
            id="smoke",
            ref="shared/smoke",
            path="topologies/beta/scenarios/smoke/scenario.yaml",
        ),
    ]
    with patch("testbed.topology_index._scenario_index_entries", return_value=dup_entries):
        try:
            get_or_build_index(tmp_path)
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected duplicate scenario_ref ValueError")

    assert "Duplicate scenario_ref detected" in message
    assert "shared/smoke" in message


def test_cli_list_groups_by_topology(tmp_path: Path, monkeypatch) -> None:
    """`testbed list` prints topology heading and scenario refs beneath each."""
    _build_fixture_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    out = result.stdout
    assert "Available scenarios:" in out
    assert "alpha" in out
    assert "  - alpha/smoke" in out
    assert "beta" in out
    assert "  - beta/discovery" in out


def test_get_or_build_index_regenerates_when_cache_files_corrupt(tmp_path: Path) -> None:
    """Corrupt index/state files are treated as missing and regenerated safely."""
    _build_fixture_tree(tmp_path)
    _, regenerated = get_or_build_index(tmp_path)
    assert regenerated is True

    state_path = tmp_path / "var/index/topologies.index.state.json"
    index_path = tmp_path / "var/index/topologies.index.yaml"
    state_path.write_text("{invalid", encoding="utf-8")
    index_path.write_text("::not-yaml", encoding="utf-8")

    _, regenerated = get_or_build_index(tmp_path)
    assert regenerated is True


def test_cli_run_resolves_scenario_ref_to_scenario_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """CLI run resolves <topology>/<scenario> ref to scenario directory from index."""
    _build_fixture_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    with patch("testbed.cli.run_scenario") as mocked_run:
        mocked_run.return_value = RunResult(
            scenario="smoke",
            passed=True,
            duration_s=0.1,
            up_node_ids=[],
            command_outputs=[],
        )
        result = runner.invoke(app, ["run", "alpha/smoke"])

    assert result.exit_code == 0
    assert mocked_run.called
    scenario_dir = mocked_run.call_args.args[0]
    assert scenario_dir == tmp_path / "topologies/alpha/scenarios/smoke"
