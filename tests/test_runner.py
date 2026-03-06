"""Tests for compose generation and runner."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from gurney.compose import CHECK_IMAGE, generate_compose
from gurney.models import (
    ArgvFactRef,
    CaptureConfig,
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
from gurney.runner import (
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


def test_compose_generation_mounts_artifacts_for_granted_nodes() -> None:
    """Nodes with scenario artifact grants get /opt/artifacts mount."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="replay",
                kind="docker",
                image="replay:local",
                network="net1",
                ip="192.168.10.2",
            ),
            NodeDef(
                name="tapirx",
                kind="docker",
                image="tapirx:local",
                network="net1",
                ip="192.168.10.3",
            ),
        ],
    )
    scenario_nodes = [
        ScenarioNodeDef(
            id="replay",
            build="run",
            networks=["net1"],
            artifacts=["dicom_echo_sample"],
        ),
        ScenarioNodeDef(id="tapirx", build="up", networks=["net1"]),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        generate_compose(top, out, scenario_nodes=scenario_nodes, check_urls=[])
        with open(out / "docker-compose.yaml") as f:
            data = yaml.safe_load(f)
        replay_vols = data["services"]["replay"].get("volumes", [])
        tapirx_vols = data["services"]["tapirx"].get("volumes", [])
        assert "${PWD}/var/artifacts:/opt/artifacts:ro" in replay_vols
        assert "${PWD}/var/artifacts:/opt/artifacts:ro" not in tapirx_vols


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
        patch("gurney.runner.compose_up", side_effect=fake_compose_up),
        patch("gurney.runner.compose_run", side_effect=fake_compose_run),
        patch("gurney.runner.compose_logs", side_effect=fake_compose_logs),
        patch("gurney.runner.compose_down", side_effect=fake_compose_down),
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
    from gurney.runner import run_scenario

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

    with patch("gurney.runner.compose_up", side_effect=fake_compose_up), patch(
        "gurney.runner.compose_run", side_effect=fake_compose_run
    ), patch("gurney.runner.compose_logs", side_effect=fake_compose_logs):
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

    with patch("gurney.runner.compose_up", return_value=("", "")), patch(
        "gurney.runner.compose_run", side_effect=fake_compose_run
    ), patch("gurney.runner.compose_logs", return_value=[]):
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


def test_run_result_with_wait_s_sleeps_after_command() -> None:
    """When a command has wait_s, runner sleeps for that duration after the command succeeds."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / ".build" / "output").mkdir(parents=True, exist_ok=True)
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
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
        name="wait-test",
        topology="testbed.yaml",
        nodes=[ScenarioNodeDef(id="replay", build="run", networks=["net1"])],
        facts=[],
        commands=[
            CommandDef(
                id="cmd-with-wait",
                node="replay",
                run=CommandRunDef(argv=["true"]),
                retry=RetryConfig(),
                wait_s=1.5,
            ),
        ],
    )

    with patch("gurney.runner.compose_up", return_value=("", "")), patch(
        "gurney.runner.compose_run", return_value=("ok", "")
    ), patch("gurney.runner.compose_logs", return_value=[]), patch(
        "gurney.runner.time.sleep"
    ) as mock_sleep:
        result = _run_compose_and_capture(
            compose_path,
            "wait-test",
            scenario_dir,
            scenario,
            topology,
            verbose=False,
            project_root=scenario_dir,
        )
    assert result.passed
    mock_sleep.assert_called_once_with(1.5)


def test_run_result_with_capture_parses_output_and_injects_env() -> None:
    """When a command has capture, output is parsed and env_override is passed to subsequent compose_run."""
    compose_path = Path("/tmp/compose.yaml")
    scenario_dir = Path("/tmp/scenario")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / ".build" / "output").mkdir(parents=True, exist_ok=True)
    topology = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="viper",
                kind="docker",
                image="viper:local",
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
        name="capture-test",
        topology="testbed.yaml",
        nodes=[
            ScenarioNodeDef(id="viper", build="up", networks=["net1"]),
            ScenarioNodeDef(id="replay", build="run", networks=["net1"]),
        ],
        facts=[],
        commands=[
            CommandDef(
                id="create-key",
                node="viper",
                run=CommandRunDef(argv=["echo", "API_KEY=secret-token-123"]),
                retry=RetryConfig(),
                capture=CaptureConfig(
                    regex="API_KEY=(\\S+)",
                    as_="viper_api_key",
                    env_var="VIPER_API_KEY",
                ),
            ),
            CommandDef(
                id="use-key",
                node="replay",
                run=CommandRunDef(argv=["sh", "-c", "echo $VIPER_API_KEY"]),
                retry=RetryConfig(),
            ),
        ],
    )

    exec_out = "API_KEY=secret-token-123"
    with patch("gurney.runner.compose_up", return_value=("", "")), patch(
        "gurney.runner.compose_exec", return_value=(exec_out, "")
    ), patch("gurney.runner.compose_run", return_value=("ok", "")) as mock_run, patch(
        "gurney.runner.compose_logs", return_value=[]
    ):
        result = _run_compose_and_capture(
            compose_path,
            "capture-test",
            scenario_dir,
            scenario,
            topology,
            verbose=False,
            project_root=scenario_dir,
        )
    assert result.passed
    mock_run.assert_called()
    calls = [c for c in mock_run.call_args_list if c[1].get("env_override")]
    assert len(calls) >= 1
    assert calls[0][1]["env_override"] == {"VIPER_API_KEY": "secret-token-123"}


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

    with patch("gurney.runner.compose_up", return_value=("", "")), patch(
        "gurney.runner.compose_run", side_effect=fake_compose_run
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

    with patch("gurney.runner.compose_up", return_value=("", "")), patch(
        "gurney.runner.compose_run", side_effect=fake_compose_run
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
    with patch("gurney.runner.compose_up", return_value=("", "")):
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
    with patch("gurney.runner.compose_up", side_effect=RuntimeError("up failed")):
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
    with patch("gurney.runner.compose_up", return_value=("", "")), patch(
        "gurney.runner.compose_logs", return_value=[("srv", "")]
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
    """After run, force_cleanup leaves no gurney.managed resources. Requires Docker."""
    from gurney.compose import force_cleanup

    force_cleanup()
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=gurney.managed=true"],
        capture_output=True,
        text=True,
    )
    assert not result.stdout.strip(), "Orphaned containers found"


def test_run_scenario_stages_fixture_pcap_into_runtime_cache(tmp_path: Path) -> None:
    """Scenario artifact refs stage topology fixture into var/artifacts before compose run."""
    topology_dir = tmp_path / "topologies" / "demo"
    scenario_dir = topology_dir / "scenarios" / "pcap-scenario"
    fixture_path = topology_dir / "fixtures" / "pcap" / "sample.pcap"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_bytes(b"fixture-bytes")

    topology_file = topology_dir / "topology.yaml"
    topology_file.parent.mkdir(parents=True, exist_ok=True)
    topology_file.write_text(
        "networks:\n"
        "  - name: net1\n"
        "    cidr: 192.168.10.0/24\n"
        "artifacts:\n"
        "  - id: sample_artifact\n"
        "    kind: pcap\n"
        "    filename: sample.pcap\n"
        "    url: https://example.invalid/sample.pcap\n"
        "nodes:\n"
        "  - name: replay\n"
        "    kind: docker\n"
        "    image: replay:local\n"
        "    network: net1\n"
        "    ip: 192.168.10.2\n",
        encoding="utf-8",
    )

    scenario_file = scenario_dir / "scenario.yaml"
    scenario_file.parent.mkdir(parents=True, exist_ok=True)
    scenario_file.write_text(
        "name: pcap-scenario\n"
        "topology: topologies/demo/topology.yaml\n"
        "nodes:\n"
        "  - id: replay\n"
        "    build: run\n"
        "    networks: [net1]\n"
        "    artifacts: [sample_artifact]\n"
        "commands:\n"
        "  - id: replay-one\n"
        "    node: replay\n"
        "    run:\n"
        "      argv: [tcpreplay, -i, eth0, {artifact: sample_artifact}]\n"
        "    retry:\n"
        "      attempts: 1\n"
        "      delay_s: 0\n",
        encoding="utf-8",
    )

    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("services: {}\n", encoding="utf-8")
    with patch("gurney.runner.generate_compose", return_value=compose_path), patch(
        "gurney.runner.compose_up", return_value=("", "")
    ), patch("gurney.runner.compose_run", return_value=("ok", "")), patch(
        "gurney.runner.compose_logs", return_value=[]
    ), patch(
        "gurney.runner.compose_down", return_value=("", "")
    ):
        result = run_scenario(scenario_dir, project_root=tmp_path)

    assert result.passed is True
    cache_path = tmp_path / "var" / "artifacts" / "sample.pcap"
    assert cache_path.exists()
    assert cache_path.read_bytes() == b"fixture-bytes"


def test_run_scenario_missing_pcap_shows_actionable_error(tmp_path: Path) -> None:
    """Missing artifact ref fails fast with fixture/cache guidance."""
    topology_dir = tmp_path / "topologies" / "demo"
    scenario_dir = topology_dir / "scenarios" / "pcap-scenario"

    topology_file = topology_dir / "topology.yaml"
    topology_file.parent.mkdir(parents=True, exist_ok=True)
    topology_file.write_text(
        "networks:\n"
        "  - name: net1\n"
        "    cidr: 192.168.10.0/24\n"
        "artifacts:\n"
        "  - id: missing_artifact\n"
        "    kind: pcap\n"
        "    filename: missing.pcap\n"
        "    url: https://example.invalid/missing.pcap\n"
        "nodes:\n"
        "  - name: replay\n"
        "    kind: docker\n"
        "    image: replay:local\n"
        "    network: net1\n"
        "    ip: 192.168.10.2\n",
        encoding="utf-8",
    )

    scenario_file = scenario_dir / "scenario.yaml"
    scenario_file.parent.mkdir(parents=True, exist_ok=True)
    scenario_file.write_text(
        "name: pcap-scenario\n"
        "topology: topologies/demo/topology.yaml\n"
        "nodes:\n"
        "  - id: replay\n"
        "    build: run\n"
        "    networks: [net1]\n"
        "    artifacts: [missing_artifact]\n"
        "commands:\n"
        "  - id: replay-one\n"
        "    node: replay\n"
        "    run:\n"
        "      argv: [tcpreplay, -i, eth0, {artifact: missing_artifact}]\n"
        "    retry:\n"
        "      attempts: 1\n"
        "      delay_s: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError) as exc:
        run_scenario(scenario_dir, project_root=tmp_path)
    msg = str(exc.value)
    assert "Missing required artifact" in msg
    assert "artifact id: missing_artifact" in msg
    assert "topologies/demo/fixtures/pcap/missing.pcap" in msg
    assert "var/artifacts/missing.pcap" in msg
    assert "run `make pull`" in msg


def test_run_scenario_rejects_ungranted_artifact_ref(tmp_path: Path) -> None:
    """Command artifact ref must be declared in scenario node artifacts grant list."""
    topology_dir = tmp_path / "topologies" / "demo"
    scenario_dir = topology_dir / "scenarios" / "artifact-scenario"
    topology_dir.mkdir(parents=True, exist_ok=True)

    (topology_dir / "topology.yaml").write_text(
        "networks:\n"
        "  - name: net1\n"
        "    cidr: 192.168.10.0/24\n"
        "artifacts:\n"
        "  - id: sample_artifact\n"
        "    kind: pcap\n"
        "    filename: sample.pcap\n"
        "    url: https://example.invalid/sample.pcap\n"
        "nodes:\n"
        "  - name: replay\n"
        "    kind: docker\n"
        "    image: replay:local\n"
        "    network: net1\n"
        "    ip: 192.168.10.2\n",
        encoding="utf-8",
    )
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario.yaml").write_text(
        "name: artifact-scenario\n"
        "topology: topologies/demo/topology.yaml\n"
        "nodes:\n"
        "  - id: replay\n"
        "    build: run\n"
        "    networks: [net1]\n"
        "commands:\n"
        "  - id: replay-one\n"
        "    node: replay\n"
        "    run:\n"
        "      argv: [tcpreplay, -i, eth0, {artifact: sample_artifact}]\n"
        "    retry:\n"
        "      attempts: 1\n"
        "      delay_s: 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not granted"):
        run_scenario(scenario_dir, project_root=tmp_path)


def test_run_scenario_rejects_unknown_node_artifact_grant(tmp_path: Path) -> None:
    """Scenario node artifact grants must reference topology artifact IDs."""
    topology_dir = tmp_path / "topologies" / "demo"
    scenario_dir = topology_dir / "scenarios" / "artifact-scenario"
    topology_dir.mkdir(parents=True, exist_ok=True)

    (topology_dir / "topology.yaml").write_text(
        "networks:\n"
        "  - name: net1\n"
        "    cidr: 192.168.10.0/24\n"
        "artifacts: []\n"
        "nodes:\n"
        "  - name: replay\n"
        "    kind: docker\n"
        "    image: replay:local\n"
        "    network: net1\n"
        "    ip: 192.168.10.2\n",
        encoding="utf-8",
    )
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario.yaml").write_text(
        "name: artifact-scenario\n"
        "topology: topologies/demo/topology.yaml\n"
        "nodes:\n"
        "  - id: replay\n"
        "    build: run\n"
        "    networks: [net1]\n"
        "    artifacts: [missing_artifact]\n"
        "commands: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown artifact"):
        run_scenario(scenario_dir, project_root=tmp_path)
