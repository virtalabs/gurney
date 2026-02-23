"""Tests for compose generation and runner."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from testbed.compose import CHECK_IMAGE, generate_compose
from testbed.models import (
    ArgvFactRef,
    CommandDef,
    CommandRunDef,
    FactDef,
    HealthCheck,
    NetworkDef,
    NodeDef,
    RetryConfig,
    ScenarioConfig,
    ScenarioNodeDef,
    TopologyConfig,
)
from testbed.runner import (
    RunEvent,
    RunResult,
    _filter_topology,
    _run_compose_and_capture,
    load_scenario,
    load_topology,
    run_scenario,
)


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
        path = generate_compose(top, out, scenario_nodes=[], check_urls=[])
        assert path.exists()
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "services" in data
        assert "redis" in data["services"]
        assert data["services"]["redis"]["image"] == "redis:7.4-alpine"
        assert "networks" in data


def test_compose_generation_includes_cap_add() -> None:
    """Compose generation emits cap_add when node has it; omits key when empty."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="with-caps",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.2",
                cap_add=["NET_ADMIN"],
            ),
            NodeDef(
                name="no-caps",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.3",
            ),
        ],
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_compose(top, out, scenario_nodes=[], check_urls=[])
        with open(out / "docker-compose.yaml") as f:
            data = yaml.safe_load(f)
        assert data["services"]["with-caps"]["cap_add"] == ["NET_ADMIN"]
        assert "cap_add" not in data["services"]["no-caps"]


def test_compose_generation_with_span() -> None:
    """With span, sender service has network_mode and no networks; others have networks."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="tapirx-live",
                kind="docker",
                image="tapirx:local",
                network="net1",
                ip="192.168.10.2",
            ),
            NodeDef(
                name="replay",
                kind="docker",
                image="replay:local",
                network="net1",
                ip=None,
            ),
        ],
    )
    scenario_nodes = [
        ScenarioNodeDef(id="tapirx-live", build="up", networks=["net1"]),
        ScenarioNodeDef(id="replay", build="run", span="tapirx-live"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_compose(top, out, scenario_nodes=scenario_nodes, check_urls=[])
        with open(out / "docker-compose.yaml") as f:
            data = yaml.safe_load(f)
        replay_svc = data["services"]["replay"]
        assert replay_svc.get("network_mode") == "service:tapirx-live"
        assert "networks" not in replay_svc
        tapirx_svc = data["services"]["tapirx-live"]
        assert "networks" in tapirx_svc
        assert "network_mode" not in tapirx_svc


def test_compose_generation_command_as_list() -> None:
    """Compose generation emits command as list of words so container receives multiple argv."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="srv",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.2",
                command="-iface eth0 -apiurl http://example.com/api/upsert -limit 200",
            ),
        ],
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_compose(top, out, scenario_nodes=[], check_urls=[])
        with open(out / "docker-compose.yaml") as f:
            data = yaml.safe_load(f)
        assert data["services"]["srv"]["command"] == [
            "-iface",
            "eth0",
            "-apiurl",
            "http://example.com/api/upsert",
            "-limit",
            "200",
        ]


def test_compose_generation_with_checks() -> None:
    """With checks, compose includes check-0, check-1 services with curl image and target network."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="blueflow-api",
                kind="docker",
                image="blueflow:local",
                network="net1",
                ip="192.168.10.4",
            ),
        ],
    )
    checks = [
        "http://blueflow-api:8000/api/assets",
        "http://blueflow-api:8000/health/",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_compose(top, out, scenario_nodes=[], check_urls=checks)
        with open(out / "docker-compose.yaml") as f:
            data = yaml.safe_load(f)
        assert "check-0" in data["services"]
        assert "check-1" in data["services"]
        check0 = data["services"]["check-0"]
        assert check0["image"] == CHECK_IMAGE
        assert "http://blueflow-api:8000/api/assets" in check0["command"]
        assert check0["depends_on"] == {"blueflow-api": {"condition": "service_started"}}
        assert check0["networks"] == {"net1": {}}
        check1 = data["services"]["check-1"]
        assert "http://blueflow-api:8000/health/" in check1["command"]


def test_compose_generation_checks_host_not_in_topology_raises() -> None:
    """generate_compose with check URL whose host is not a topology node raises."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="api",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.2",
            ),
        ],
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        with pytest.raises(ValueError, match="must match a topology node"):
            generate_compose(
                top, out, scenario_nodes=[], check_urls=["http://other-host:8000/health"]
            )


def test_compose_generation_with_environment_overrides() -> None:
    """Scenario env_from_fact overrides merge into node env; override wins over topology value."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="api",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.2",
                environment={"BASE": "from-topology", "OVERME": "topology-value"},
            ),
            NodeDef(
                name="worker",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.3",
                environment={"WORKER_ONLY": "yes"},
            ),
        ],
    )
    scenario_nodes = [
        ScenarioNodeDef(
            id="api",
            build="up",
            networks=["net1"],
            env_from_fact="api_env",
        ),
        ScenarioNodeDef(id="worker", build="up", networks=["net1"]),
    ]
    scenario_facts = [
        FactDef(name="api_env", value={"OVERME": "scenario-value", "EXTRA": "scenario-only"}),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_compose(
            top, out,
            scenario_nodes=scenario_nodes,
            scenario_facts=scenario_facts,
            check_urls=[],
        )
        with open(out / "docker-compose.yaml") as f:
            data = yaml.safe_load(f)
        api_env = {k: v for k, v in (e.split("=", 1) for e in data["services"]["api"]["environment"])}
        worker_env = {k: v for k, v in (e.split("=", 1) for e in data["services"]["worker"]["environment"])}
        assert api_env["BASE"] == "from-topology"
        assert api_env["OVERME"] == "scenario-value"
        assert api_env["EXTRA"] == "scenario-only"
        assert worker_env == {"WORKER_ONLY": "yes"}
        assert "EXTRA" not in worker_env


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


def test_filter_topology_rejects_unknown_nodes() -> None:
    """Filtering with a node name not in the topology raises ValueError."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="a", kind="docker", image="x", network="net1", ip="192.168.10.2"
            ),
        ],
    )
    with pytest.raises(ValueError) as exc_info:
        _filter_topology(top, ["a", "typo-node"])
    assert "typo-node" in str(exc_info.value)
    assert "Valid nodes" in str(exc_info.value)


def test_run_scenario_rejects_span_node_not_in_topology() -> None:
    """When span target is not in topology, run_scenario raises ValueError."""
    with tempfile.TemporaryDirectory() as tmp:
        scenario_dir = Path(tmp) / "scenario"
        scenario_dir.mkdir()
        # Topology has only "replay"; scenario node replay has span: tapirx-live (missing)
        top_path = scenario_dir / "topology.yaml"
        top_path.write_text(
            """
networks:
  - name: net1
    cidr: 192.168.10.0/24
nodes:
  - name: replay
    kind: docker
    image: img
    network: net1
    ip: 192.168.10.2
"""
        )
        (scenario_dir / "scenario.yaml").write_text(
            """
name: span-invalid
topology: topology.yaml
facts: []
nodes:
  - id: replay
    build: run
    span: tapirx-live
commands: []
"""
        )
        with pytest.raises(ValueError) as exc_info:
            run_scenario(scenario_dir, project_root=scenario_dir)
        msg = str(exc_info.value)
        assert "tapirx-live" in msg
        assert "topology" in msg.lower() or "valid" in msg.lower()


def test_run_scenario_unknown_node_id_raises() -> None:
    """When scenario nodes reference a node id not in topology, run_scenario raises."""
    with tempfile.TemporaryDirectory() as tmp:
        scenario_dir = Path(tmp) / "scenario"
        scenario_dir.mkdir()
        (scenario_dir / "topology.yaml").write_text(
            """
networks:
  - name: net1
    cidr: 192.168.10.0/24
nodes:
  - name: api
    kind: docker
    image: img
    network: net1
    ip: 192.168.10.2
"""
        )
        (scenario_dir / "scenario.yaml").write_text(
            """
name: node-invalid
topology: topology.yaml
facts: []
nodes:
  - id: api
    build: up
    networks: [net1]
  - id: typo-node
    build: up
    networks: [net1]
commands: []
"""
        )
        with pytest.raises(ValueError) as exc_info:
            run_scenario(scenario_dir, project_root=scenario_dir)
        msg = str(exc_info.value)
        assert "typo-node" in msg
        assert "topology" in msg.lower() or "Valid" in msg


def test_load_scenario() -> None:
    """Load scenario from scenarios/smoke-minimal (v2)."""
    root = Path(__file__).resolve().parent.parent
    scenario_dir = root / "scenarios" / "smoke-minimal"
    if not scenario_dir.exists():
        pytest.skip("scenarios/smoke-minimal not found")
    s = load_scenario(scenario_dir)
    assert s.name == "smoke-minimal"
    node_ids = [n.id for n in s.nodes]
    assert node_ids == ["postgres", "redis", "pacs-server"]


def test_load_topology() -> None:
    """Load topology from testbed.yaml."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "testbed.yaml").exists():
        pytest.skip("testbed.yaml not found")
    top = load_topology("testbed.yaml", base_dir=root)
    assert len(top.networks) >= 1
    assert len(top.nodes) >= 1


@pytest.mark.skipif(
    not (Path.cwd() / "scenarios" / "smoke-minimal").exists(),
    reason="smoke-minimal scenario required",
)
def test_run_scenario_emits_events_to_handler() -> None:
    """When event_handler is provided, run_scenario emits compose_up_started, service_up, etc."""
    root = Path.cwd()
    scenario_dir = root / "scenarios" / "smoke-minimal"
    events: list[RunEvent] = []

    def collect(e: RunEvent) -> None:
        events.append(e)

    def fake_compose_up(path: Path, verbose: bool = False, services: list | None = None, **kwargs: object):
        return ("", "")

    def fake_compose_run(path: Path, service: str, **kwargs: object):
        return ("", "")

    def fake_compose_logs(path: Path, services: list[str]):
        return [(s, "") for s in services]

    def fake_compose_down(path: Path) -> None:
        pass

    with (
        patch("testbed.runner.compose_up", side_effect=fake_compose_up),
        patch("testbed.runner.compose_run", side_effect=fake_compose_run),
        patch("testbed.runner.compose_logs", side_effect=fake_compose_logs),
        patch("testbed.runner.compose_down", side_effect=fake_compose_down),
    ):
        result = run_scenario(
            scenario_dir,
            project_root=root,
            keep=True,
            event_handler=collect,
        )
    assert result.passed
    kinds = [e.kind for e in events]
    assert "compose_up_started" in kinds
    assert kinds.count("service_up") >= 1
    # smoke-minimal has postgres, redis, pacs-server as up nodes
    service_ups = [e.get("node_id") for e in events if e.kind == "service_up"]
    assert "postgres" in service_ups or len(service_ups) >= 1


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


def test_run_result_verbose_fields_on_success_with_verbose() -> None:
    """When verbose=True and run succeeds, RunResult has compose_up and command_outputs."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / ".build" / "output").mkdir(parents=True, exist_ok=True)
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="mock-asset-api",
                kind="docker",
                image="mock:local",
                network="net1",
                ip="192.168.10.2",
                healthcheck=HealthCheck(
                    test=["CMD", "true"],
                    interval_s=5,
                    timeout_s=3,
                    retries=3,
                ),
            ),
            NodeDef(
                name="tapirx-pcap",
                kind="docker",
                image="tapirx:local",
                network="net1",
                ip=None,
            ),
            NodeDef(
                name="replay",
                kind="docker",
                image="replay:local",
                network="net1",
                ip=None,
            ),
        ],
    )
    scenario = ScenarioConfig(
        name="tapirx-dicom-discovery",
        topology="testbed.yaml",
        nodes=[
            ScenarioNodeDef(id="mock-asset-api", build="up", networks=["net1"]),
            ScenarioNodeDef(id="tapirx-pcap", build="run", networks=["net1"]),
            ScenarioNodeDef(id="replay", build="run", networks=["net1"]),
        ],
        facts=[],
        commands=[
            CommandDef(
                id="cmd-replay",
                node="replay",
                run=CommandRunDef(argv=["true"]),
                retry=RetryConfig(),
            ),
            CommandDef(
                id="cmd-tapirx",
                node="tapirx-pcap",
                run=CommandRunDef(argv=["true"]),
                retry=RetryConfig(),
            ),
        ],
    )

    def fake_compose_up(path: Path, verbose: bool = False, services: list | None = None):
        return ("compose up out", "compose up err")

    def fake_compose_run(
        path: Path, service: str, verbose: bool = False, **kwargs: object
    ):
        return (f"{service} stdout", f"{service} stderr")

    def fake_compose_logs(path: Path, services: list[str]):
        return [(svc, f"{svc} logs") for svc in services]

    with patch("testbed.runner.compose_up", side_effect=fake_compose_up), patch(
        "testbed.runner.compose_run", side_effect=fake_compose_run
    ), patch("testbed.runner.compose_logs", side_effect=fake_compose_logs):
        result = _run_compose_and_capture(
            compose_path,
            "tapirx-dicom-discovery",
            scenario_dir,
            scenario,
            topology,
            verbose=True,
            project_root=scenario_dir,
        )
    assert result.passed
    assert result.up_node_ids == ["mock-asset-api"]
    assert result.compose_up_stdout == "compose up out"
    assert result.compose_up_stderr == "compose up err"
    assert result.command_outputs is not None
    assert len(result.command_outputs) == 2
    assert result.command_outputs[0] == ("cmd-replay", ["true"], "replay stdout", "replay stderr")
    assert result.command_outputs[1] == ("cmd-tapirx", ["true"], "tapirx-pcap stdout", "tapirx-pcap stderr")
    assert result.compose_up_service_logs is not None
    assert result.compose_up_service_logs == [("mock-asset-api", "mock-asset-api logs")]
    assert result.output_files is not None
    assert result.output_files == []


def test_run_result_with_http_check_captures_output() -> None:
    """When scenario has http_check command, runner runs check-0 and captures output when verbose."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / ".build" / "output").mkdir(parents=True, exist_ok=True)
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="blueflow-api",
                kind="docker",
                image="blueflow:local",
                network="net1",
                ip="192.168.10.2",
            ),
            NodeDef(
                name="replay",
                kind="docker",
                image="replay:local",
                network="net1",
                ip=None,
            ),
        ],
    )
    scenario = ScenarioConfig(
        name="tapirx-dicom-discovery",
        topology="testbed.yaml",
        nodes=[
            ScenarioNodeDef(id="blueflow-api", build="up", networks=["net1"]),
            ScenarioNodeDef(id="replay", build="run", networks=["net1"]),
        ],
        facts=[FactDef(name="assets_url", value="http://blueflow-api:8000/api/assets")],
        commands=[
            CommandDef(
                id="replay-cmd",
                node="replay",
                run=CommandRunDef(argv=["true"]),
                retry=RetryConfig(),
            ),
            CommandDef(
                id="verify-assets",
                node="blueflow-api",
                run=CommandRunDef(
                    argv=["http_check", ArgvFactRef(fact="assets_url")]
                ),
                retry=RetryConfig(),
            ),
        ],
    )

    run_calls: list[str] = []

    def fake_compose_run(
        path: Path, service: str, verbose: bool = False, **kwargs: object
    ):
        run_calls.append(service)
        if service == "check-0":
            return ("[]\n200", "")
        return (f"{service} stdout", f"{service} stderr")

    with patch("testbed.runner.compose_up", return_value=("", "")), patch(
        "testbed.runner.compose_run", side_effect=fake_compose_run
    ), patch("testbed.runner.compose_logs", return_value=[]):
        result = _run_compose_and_capture(
            compose_path,
            "tapirx-dicom-discovery",
            scenario_dir,
            scenario,
            topology,
            verbose=True,
            project_root=scenario_dir,
        )
    assert result.passed
    assert result.up_node_ids == ["blueflow-api"]
    assert run_calls == ["replay", "check-0"]
    assert result.command_outputs is not None
    assert result.command_outputs[0][0] == "replay-cmd"
    assert result.command_outputs[1][0] == "verify-assets"
    assert result.command_outputs[1][2] == "[]\n200"


def test_run_result_check_failure_sets_passed_false() -> None:
    """When a check compose_run fails, RunResult.passed is False and error mentions the URL."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / ".build" / "output").mkdir(parents=True, exist_ok=True)
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="blueflow-api",
                kind="docker",
                image="blueflow:local",
                network="net1",
                ip="192.168.10.2",
            ),
            NodeDef(
                name="replay",
                kind="docker",
                image="replay:local",
                network="net1",
                ip=None,
            ),
        ],
    )
    scenario = ScenarioConfig(
        name="tapirx-dicom-discovery",
        topology="testbed.yaml",
        nodes=[
            ScenarioNodeDef(id="blueflow-api", build="up", networks=["net1"]),
            ScenarioNodeDef(id="replay", build="run", networks=["net1"]),
        ],
        facts=[FactDef(name="url", value="http://blueflow-api:8000/api/assets")],
        commands=[
            CommandDef(
                id="replay-cmd",
                node="replay",
                run=CommandRunDef(argv=["true"]),
                retry=RetryConfig(),
            ),
            CommandDef(
                id="verify",
                node="blueflow-api",
                run=CommandRunDef(argv=["http_check", ArgvFactRef(fact="url")]),
                retry=RetryConfig(),
            ),
        ],
    )

    def fake_compose_run(
        path: Path, service: str, verbose: bool = False, **kwargs: object
    ):
        if service == "check-0":
            raise RuntimeError("docker compose run check-0 failed: connection refused")
        return ("", "")

    with patch("testbed.runner.compose_up", return_value=("", "")), patch(
        "testbed.runner.compose_run", side_effect=fake_compose_run
    ):
        result = _run_compose_and_capture(
            compose_path,
            "tapirx-dicom-discovery",
            scenario_dir,
            scenario,
            topology,
            verbose=False,
            project_root=scenario_dir,
        )
    assert not result.passed
    assert result.up_node_ids == ["blueflow-api"]
    assert result.command_outputs is not None
    assert len(result.command_outputs) == 2
    assert result.command_outputs[0][0] == "replay-cmd"
    assert result.command_outputs[1][0] == "verify"
    assert "failed" in (result.error or "").lower() or "check" in (result.error or "").lower()


def test_run_result_check_non_2xx_sets_passed_false() -> None:
    """When check returns HTTP non-2xx, runner raises and RunResult.passed is False."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / ".build" / "output").mkdir(parents=True, exist_ok=True)
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="blueflow-api",
                kind="docker",
                image="blueflow:local",
                network="net1",
                ip="192.168.10.2",
            ),
            NodeDef(
                name="replay",
                kind="docker",
                image="replay:local",
                network="net1",
                ip=None,
            ),
        ],
    )
    scenario = ScenarioConfig(
        name="tapirx-dicom-discovery",
        topology="testbed.yaml",
        nodes=[
            ScenarioNodeDef(id="blueflow-api", build="up", networks=["net1"]),
            ScenarioNodeDef(id="replay", build="run", networks=["net1"]),
        ],
        facts=[FactDef(name="url", value="http://blueflow-api:8000/api/assets/")],
        commands=[
            CommandDef(
                id="replay-cmd",
                node="replay",
                run=CommandRunDef(argv=["true"]),
                retry=RetryConfig(),
            ),
            CommandDef(
                id="verify",
                node="blueflow-api",
                run=CommandRunDef(argv=["http_check", ArgvFactRef(fact="url")]),
                retry=RetryConfig(),
            ),
        ],
    )

    def fake_compose_run(
        path: Path, service: str, verbose: bool = False, **kwargs: object
    ):
        if service == "check-0":
            # Simulate curl -w "\n%{http_code}" output: body + last line = status
            return ("Not found\n404", "")
        return ("", "")

    with patch("testbed.runner.compose_up", return_value=("", "")), patch(
        "testbed.runner.compose_run", side_effect=fake_compose_run
    ):
        result = _run_compose_and_capture(
            compose_path,
            "tapirx-dicom-discovery",
            scenario_dir,
            scenario,
            topology,
            verbose=False,
            project_root=scenario_dir,
        )
    assert not result.passed
    assert result.up_node_ids == ["blueflow-api"]
    assert result.command_outputs is not None
    assert len(result.command_outputs) == 2
    assert result.command_outputs[1][0] == "verify"
    assert "returned HTTP 404" in (result.error or "")
    assert "http://blueflow-api:8000/api/assets/" in (result.error or "")


def test_run_result_verbose_fields_none_when_not_verbose() -> None:
    """When verbose=False, RunResult has no verbose outputs."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario = ScenarioConfig(
        name="minimal",
        nodes=[ScenarioNodeDef(id="srv", build="up", networks=["net1"])],
        commands=[],
    )
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="srv",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.2",
            )
        ],
    )
    with patch("testbed.runner.compose_up", return_value=("", "")):
        result = _run_compose_and_capture(
            compose_path,
            "minimal",
            scenario_dir,
            scenario,
            topology,
            verbose=False,
            project_root=scenario_dir,
        )
    assert result.passed
    assert result.up_node_ids == ["srv"]
    assert result.command_outputs == []
    assert result.compose_up_stdout is None
    assert result.compose_up_stderr is None
    assert result.compose_up_service_logs is None
    assert result.output_files is None


def test_run_result_verbose_fields_none_on_failure() -> None:
    """On failure, RunResult has no verbose outputs."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario = ScenarioConfig(
        name="minimal",
        nodes=[ScenarioNodeDef(id="srv", build="up", networks=["net1"])],
        commands=[],
    )
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="srv",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.2",
            )
        ],
    )
    with patch("testbed.runner.compose_up", side_effect=RuntimeError("up failed")):
        result = _run_compose_and_capture(
            compose_path,
            "minimal",
            scenario_dir,
            scenario,
            topology,
            verbose=True,
            project_root=scenario_dir,
        )
    assert not result.passed
    assert result.error == "up failed"
    assert result.up_node_ids == ["srv"]
    assert result.command_outputs == []
    assert result.compose_up_stdout is None
    assert result.compose_up_stderr is None
    assert result.compose_up_service_logs is None
    assert result.output_files is None


def test_gather_output_files_in_verbose_result(tmp_path: Path) -> None:
    """When verbose and .build/output has readable files, RunResult.output_files contains them."""
    output_dir = tmp_path / ".build" / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "assets.jsonl").write_text('{"id":"a"}\n')
    (output_dir / "notes.txt").write_text("done\n")
    (output_dir / "binary.bin").write_bytes(b"\x00\x01")  # skipped

    compose_path = tmp_path / "compose.yaml"
    scenario = ScenarioConfig(
        name="any",
        nodes=[ScenarioNodeDef(id="srv", build="up", networks=["net1"])],
        commands=[],
    )
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="srv",
                kind="docker",
                image="img",
                network="net1",
                ip="192.168.10.2",
            )
        ],
    )
    with patch("testbed.runner.compose_up", return_value=("", "")), patch(
        "testbed.runner.compose_logs", return_value=[("srv", "")]
    ):
        result = _run_compose_and_capture(
            compose_path,
            "any",
            tmp_path,
            scenario,
            topology,
            verbose=True,
            project_root=tmp_path,
        )
    assert result.passed
    assert result.output_files is not None
    names = [n for n, _ in result.output_files]
    assert "assets.jsonl" in names
    assert "notes.txt" in names
    assert "binary.bin" not in names
    content_by_name = dict(result.output_files)
    assert content_by_name["assets.jsonl"] == '{"id":"a"}\n'
    assert content_by_name["notes.txt"] == "done\n"


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
