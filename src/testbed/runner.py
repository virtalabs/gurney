"""Scenario orchestration: parse, generate compose, up, teardown."""

import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from testbed.compose import compose_down, compose_up, generate_compose
from testbed.models import NodeDef, ScenarioConfig, TopologyConfig


@dataclass
class RunResult:
    """Result of a scenario run."""

    scenario: str
    passed: bool
    duration_s: float
    error: str | None = None


def load_scenario(scenario_dir: Path) -> ScenarioConfig:
    """Load scenario.yaml from scenario directory."""
    path = scenario_dir / "scenario.yaml"
    if not path.exists():
        raise FileNotFoundError(f"scenario.yaml not found in {scenario_dir}")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return ScenarioConfig(**data)


def load_topology(
    topology_file: str, base_dir: Path | None = None
) -> TopologyConfig:
    """Load testbed.yaml topology. base_dir is typically the project root."""
    base = base_dir or Path.cwd()
    path = base / topology_file
    if not path.exists():
        raise FileNotFoundError(f"Topology file not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return TopologyConfig(**data)


def _closure_deps(names: set[str], nodes: list[NodeDef]) -> set[str]:
    """Return names plus all transitive dependencies."""
    result = set(names)
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node.name not in result:
                continue
            for dep in node.depends_on:
                if dep not in result:
                    result.add(dep)
                    changed = True
    return result


def _filter_topology(topology: TopologyConfig, node_names: list[str]) -> TopologyConfig:
    """Filter topology to only include given nodes and their dependencies (transitive)."""
    node_set = _closure_deps(set(node_names), topology.nodes)
    filtered_nodes = [n for n in topology.nodes if n.name in node_set]
    network_names = {n.network for n in filtered_nodes}
    filtered_networks = [n for n in topology.networks if n.name in network_names]
    return TopologyConfig(networks=filtered_networks, nodes=filtered_nodes)


def _run_compose_and_capture(
    compose_path: Path, scenario_name: str, verbose: bool
) -> RunResult:
    """Run compose up, return RunResult. Does not teardown."""
    start = time.perf_counter()
    try:
        compose_up(compose_path, verbose=verbose)
        return RunResult(
            scenario=scenario_name, passed=True, duration_s=time.perf_counter() - start
        )
    except Exception as exc:
        return RunResult(
            scenario=scenario_name,
            passed=False,
            duration_s=time.perf_counter() - start,
            error=str(exc),
        )


def run_scenario(
    scenario_dir: Path,
    *,
    keep: bool = False,
    verbose: bool = False,
    project_root: Path | None = None,
) -> RunResult:
    """Run a scenario: parse, generate compose, up, teardown (unless --keep)."""
    base = project_root or Path.cwd()
    scenario = load_scenario(scenario_dir)
    topology = load_topology(scenario.topology, base_dir=base)

    if scenario.nodes is not None:
        topology = _filter_topology(topology, scenario.nodes)

    compose_path = generate_compose(topology, scenario_dir / ".build")
    result = _run_compose_and_capture(compose_path, scenario.name, verbose)

    if not keep:
        try:
            compose_down(compose_path)
        except Exception:
            pass

    return result
