"""Scenario orchestration: parse, generate compose, up, teardown."""

import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from testbed.compose import (
    compose_down,
    compose_logs,
    compose_run,
    compose_up,
    generate_compose,
)
from testbed.models import NodeDef, ScenarioConfig, TopologyConfig

# Extensions treated as readable text for verbose output
OUTPUT_READABLE_SUFFIXES = (".txt", ".json", ".jsonl", ".yaml", ".yml", ".log")


@dataclass
class RunResult:
    """Result of a scenario run."""

    scenario: str
    passed: bool
    duration_s: float
    error: str | None = None
    # Verbose output (only set when verbose=True and run succeeded)
    compose_up_stdout: str | None = None
    compose_up_stderr: str | None = None
    run_once_outputs: list[tuple[str, str, str]] | None = None  # (service, stdout, stderr)
    output_files: list[tuple[str, str]] | None = None  # (relative_path, content)
    compose_up_service_logs: list[tuple[str, str]] | None = None  # (service_name, logs)


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
    topology_node_names = {n.name for n in topology.nodes}
    unknown = [n for n in node_names if n not in topology_node_names]
    if unknown:
        raise ValueError(
            f"Scenario nodes reference unknown topology nodes: {unknown}. "
            f"Valid nodes: {sorted(topology_node_names)}"
        )
    node_set = _closure_deps(set(node_names), topology.nodes)
    filtered_nodes = [n for n in topology.nodes if n.name in node_set]
    network_names = {n.network for n in filtered_nodes}
    filtered_networks = [n for n in topology.networks if n.name in network_names]
    return TopologyConfig(networks=filtered_networks, nodes=filtered_nodes)


def _run_run_once(
    compose_path: Path, run_once: list[str], verbose: bool
) -> list[tuple[str, str, str]]:
    """Run each run_once service as a one-off after compose up.
    Returns [(service_name, stdout, stderr), ...]."""
    outputs: list[tuple[str, str, str]] = []
    for svc in run_once:
        out, err = compose_run(compose_path, svc, verbose=verbose)
        outputs.append((svc, out, err))
    return outputs


def _gather_output_files(output_dir: Path) -> list[tuple[str, str]]:
    """Collect readable text files from output_dir. Returns [(relative_path, content), ...]."""
    if not output_dir.is_dir():
        return []
    result: list[tuple[str, str]] = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in OUTPUT_READABLE_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            result.append((path.name, content))
        except OSError:
            continue
    return result


def _run_compose_and_capture(
    compose_path: Path,
    scenario_name: str,
    scenario_dir: Path,
    scenario: ScenarioConfig,
    topology: TopologyConfig,
    verbose: bool,
) -> RunResult:
    """Run compose up, run_once (if any), assertions, return RunResult. Does not teardown."""
    start = time.perf_counter()
    try:
        compose_up_stdout, compose_up_stderr = "", ""
        run_once_outputs: list[tuple[str, str, str]] = []

        if scenario.run_once:
            up_services = [
                n.name for n in topology.nodes if n.name not in scenario.run_once
            ]
            compose_up_stdout, compose_up_stderr = compose_up(
                compose_path, verbose=verbose, services=up_services
            )
            run_once_outputs = _run_run_once(
                compose_path, scenario.run_once, verbose=verbose
            )
        else:
            up_services = [n.name for n in topology.nodes]
            compose_up_stdout, compose_up_stderr = compose_up(
                compose_path, verbose=verbose
            )

        compose_up_service_logs: list[tuple[str, str]] | None = None
        if verbose and up_services:
            compose_up_service_logs = compose_logs(compose_path, up_services)

        output_dir = scenario_dir / ".build" / "output"
        output_files = _gather_output_files(output_dir) if verbose else []

        return RunResult(
            scenario=scenario_name,
            passed=True,
            duration_s=time.perf_counter() - start,
            compose_up_stdout=compose_up_stdout if verbose else None,
            compose_up_stderr=compose_up_stderr if verbose else None,
            run_once_outputs=run_once_outputs if verbose else None,
            output_files=output_files if verbose else None,
            compose_up_service_logs=compose_up_service_logs,
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

    if scenario.span:
        valid = [n.name for n in topology.nodes]
        for entry in scenario.span:
            for name, role in [(entry.sender, "sender"), (entry.listener, "listener")]:
                if name not in valid:
                    raise ValueError(
                        f"Scenario span {role} '{name}' is not in topology. "
                        f"Valid node names: {valid}"
                    )

    build_dir = scenario_dir / ".build"
    (build_dir / "output").mkdir(parents=True, exist_ok=True)
    compose_path = generate_compose(
        topology, build_dir, verbose=verbose, span=scenario.span
    )
    result = _run_compose_and_capture(
        compose_path,
        scenario.name,
        scenario_dir,
        scenario,
        topology,
        verbose,
    )

    if not keep:
        try:
            compose_down(compose_path)
        except Exception:
            pass

    return result
