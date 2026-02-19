"""Tests for compose generation and runner."""

import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from testbed.compose import generate_compose
from testbed.models import (
    HealthCheck,
    NetworkDef,
    NodeDef,
    TopologyConfig,
)
from testbed.runner import _filter_topology, load_scenario, load_topology


def test_compose_generation() -> None:
    """Generate compose file from topology, assert YAML structure."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="redis",
                kind="docker",
                image="redis:7.4-alpine",
                network="net1",
                ip="192.168.10.2",
                ports=[6379],
                healthcheck=HealthCheck(
                    test=["CMD", "redis-cli", "ping"],
                    interval_s=5,
                    timeout_s=3,
                    retries=3,
                ),
            )
        ],
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        path = generate_compose(top, out)
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "services" in data
        assert "redis" in data["services"]
        assert data["services"]["redis"]["image"] == "redis:7.4-alpine"
        assert "networks" in data


def test_filter_topology_includes_dependencies() -> None:
    """Filtering by nodes includes transitive dependencies."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="a", kind="docker", image="x", network="net1", ip="192.168.10.2"
            ),
            NodeDef(
                name="b",
                kind="docker",
                image="x",
                network="net1",
                ip="192.168.10.3",
                depends_on=["a"],
            ),
            NodeDef(
                name="c",
                kind="docker",
                image="x",
                network="net1",
                ip="192.168.10.4",
                depends_on=["b"],
            ),
        ],
    )
    filtered = _filter_topology(top, ["c"])
    names = {n.name for n in filtered.nodes}
    assert names == {"a", "b", "c"}


def test_load_scenario() -> None:
    """Load scenario from scenarios/smoke-minimal."""
    root = Path(__file__).resolve().parent.parent
    scenario_dir = root / "scenarios" / "smoke-minimal"
    if not scenario_dir.exists():
        pytest.skip("scenarios/smoke-minimal not found")
    s = load_scenario(scenario_dir)
    assert s.name == "smoke-minimal"
    assert s.nodes == ["postgres", "redis", "pacs-server"]


def test_load_topology() -> None:
    """Load topology from testbed.yaml."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "testbed.yaml").exists():
        pytest.skip("testbed.yaml not found")
    top = load_topology("testbed.yaml", base_dir=root)
    assert len(top.networks) >= 1
    assert len(top.nodes) >= 1


@pytest.mark.slow
@pytest.mark.skipif(
    not (Path.cwd() / "scenarios" / "smoke-minimal").exists(),
    reason="smoke-minimal scenario required",
)
def test_scenario_runs_twice() -> None:
    """Run smoke-minimal twice; both should pass. Requires Docker."""
    from testbed.runner import run_scenario

    root = Path.cwd()
    scenario_dir = root / "scenarios" / "smoke-minimal"
    r1 = run_scenario(scenario_dir, project_root=root)
    r2 = run_scenario(scenario_dir, project_root=root)
    assert r1.passed
    assert r2.passed


def test_teardown_no_orphans() -> None:
    """After run, force_cleanup leaves no testbed.managed resources. Requires Docker."""
    from testbed.compose import force_cleanup

    force_cleanup()
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=testbed.managed=true"],
        capture_output=True,
        text=True,
    )
    assert not result.stdout.strip(), "Orphaned containers found"
