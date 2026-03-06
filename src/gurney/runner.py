"""Scenario orchestration: parse, generate compose, up, teardown."""

import logging
import re
import shutil
import time
from filecmp import cmp
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from gurney.compose import (
    compose_down,
    compose_exec,
    compose_logs,
    compose_run,
    compose_up,
    generate_compose,
)
from gurney.models import (
    ArgvArtifactRef,
    FactDef,
    NodeDef,
    ScenarioConfig,
    TopologyArtifactDef,
    TopologyConfig,
    resolve_argv,
)
from gurney.schema_validation import validate_scenario_file, validate_topology_file
from gurney.utility import ensure_log_dir

logger = logging.getLogger(__name__)

# Extensions treated as readable text for verbose output
OUTPUT_READABLE_SUFFIXES = (".txt", ".json", ".jsonl", ".yaml", ".yml", ".log")
RUNTIME_ARTIFACT_DIR = Path("var") / "artifacts"

# (command_id, resolved_argv, stdout, stderr)
CommandOutputItem = tuple[str, list[str], str, str]


@dataclass(frozen=True)
class RunEvent:
    """Lightweight event emitted during a scenario run for live UI or logging."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


EventHandler = Callable[[RunEvent], None]


@dataclass
class RunResult:
    """Result of a scenario run."""

    scenario: str
    passed: bool
    duration_s: float
    error: str | None = None
    # Default output: always set when runner has data (success or partial failure)
    up_node_ids: list[str] | None = None  # ordered node ids with build == "up"
    command_outputs: list[CommandOutputItem] | None = None
    # Verbose output (only set when verbose=True and run succeeded)
    compose_up_stdout: str | None = None
    compose_up_stderr: str | None = None
    output_files: list[tuple[str, str]] | None = None  # (relative_path, content)
    compose_up_service_logs: list[tuple[str, str]] | None = None  # (service_name, logs)


def load_scenario(scenario_dir: Path) -> ScenarioConfig:
    """Load scenario.yaml from scenario directory."""
    path = scenario_dir / "scenario.yaml"
    if not path.exists():
        raise FileNotFoundError(f"scenario.yaml not found in {scenario_dir}")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    validate_scenario_file(path)
    logger.debug("Loaded scenario from %s", path)
    return ScenarioConfig(**data)


def load_topology(topology_file: str, base_dir: Path | None = None) -> TopologyConfig:
    """Load testbed.yaml topology. base_dir is typically the project root."""
    base = base_dir or Path.cwd()
    path = base / topology_file
    if not path.exists():
        raise FileNotFoundError(f"Topology file not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    validate_topology_file(path)
    logger.debug("Loaded topology from %s", path)
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
    return TopologyConfig(
        networks=filtered_networks,
        artifacts=topology.artifacts,
        nodes=filtered_nodes,
    )


def _facts_by_name(scenario: ScenarioConfig) -> dict:
    """Return facts as name -> FactDef for resolution."""
    return {f.name: f for f in scenario.facts}


def _emit(handler: EventHandler | None, kind: str, **payload: Any) -> None:
    """Emit a run event if handler is set."""
    if handler is not None:
        handler(RunEvent(kind=kind, payload=dict(payload)))


def _emit_command_ended(
    event_handler: EventHandler | None,
    *,
    command_id: str,
    argv: list[str],
    success: bool,
    stdout: str,
    stderr: str,
    error: str | None = None,
) -> None:
    """Emit normalized command_ended event payload."""
    payload: dict[str, Any] = {
        "command_id": command_id,
        "argv": argv,
        "success": success,
        "stdout": stdout,
        "stderr": stderr,
    }
    if error is not None:
        payload["error"] = error
    _emit(event_handler, "command_ended", **payload)


def _run_command_once(
    compose_path: Path,
    service_name: str,
    *,
    verbose: bool,
    no_deps: bool,
    command_argv: list[str],
    use_exec: bool,
    detached: bool,
    env_override: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Run one command attempt using compose_run or compose_exec."""
    if use_exec:
        return compose_exec(
            compose_path,
            service_name,
            command_argv,
            detached=detached,
        )
    return compose_run(
        compose_path,
        service_name,
        verbose=verbose,
        no_deps=no_deps,
        command=command_argv,
        env_override=env_override,
    )


def _run_command_with_retry(
    compose_path: Path,
    command_id: str,
    service_name: str,
    retry_attempts: int,
    retry_delay_s: float,
    verbose: bool,
    no_deps: bool = False,
    command_argv: list[str] | None = None,
    use_exec: bool = False,
    detached: bool = False,
    env_override: dict[str, str] | None = None,
    event_handler: EventHandler | None = None,
) -> tuple[bool, str, str, str | None]:
    """Run a service with retry policy. Returns (success, stdout, stderr, error_msg)."""
    last_out, last_err = "", ""
    argv = command_argv or []
    _emit(event_handler, "command_started", command_id=command_id, argv=argv)
    for attempt in range(retry_attempts):
        if attempt > 0:
            time.sleep(retry_delay_s)
        try:
            last_out, last_err = _run_command_once(
                compose_path,
                service_name,
                verbose=verbose,
                no_deps=no_deps,
                command_argv=argv,
                use_exec=use_exec,
                detached=detached,
                env_override=env_override,
            )
            _emit_command_ended(
                event_handler,
                command_id=command_id,
                argv=argv,
                success=True,
                stdout=last_out,
                stderr=last_err,
            )
            return (True, last_out, last_err, None)
        except RuntimeError as exc:
            if attempt == retry_attempts - 1:
                logger.error(
                    "Command %s failed after %s attempt(s): %s",
                    command_id,
                    retry_attempts,
                    exc,
                )
                error_msg = f"Command {command_id!r} failed after {retry_attempts} attempt(s): {exc}"
                _emit_command_ended(
                    event_handler,
                    command_id=command_id,
                    argv=argv,
                    success=False,
                    stdout=last_out,
                    stderr=last_err,
                    error=str(exc),
                )
                return (False, last_out, last_err, error_msg)
    return (True, last_out, last_err, None)


def _parse_http_check_output(out: str) -> tuple[str, int, str]:
    """Parse http_check output into (status_text, status_code, body_preview)."""
    lines = (out or "").strip().split("\n")
    code_str = lines[-1].strip() if lines else ""
    code = int(code_str) if code_str.isdigit() else 0
    body_preview = ""
    if len(lines) > 1:
        body = "\n".join(lines[:-1]).strip()
        body_preview = f". Response preview: {body[:400]!r}"
    return (code_str, code, body_preview)


def _finalize_check_failure(
    event_handler: EventHandler | None,
    *,
    command_id: str,
    url: str,
    out: str,
    err: str,
    message: str,
) -> tuple[bool, str, str, str]:
    """Emit/log and return normalized check failure tuple."""
    logger.error("%s", message)
    _emit(event_handler, "check_failed", command_id=command_id, url=url, error=message)
    return (False, out, err, message)


def _run_check_service(
    compose_path: Path,
    check_name: str,
    url: str,
    verbose: bool,
    retry_attempts: int = 1,
    retry_delay_s: float = 0.0,
    event_handler: EventHandler | None = None,
    command_id: str | None = None,
) -> tuple[bool, str, str, str | None]:
    """Run a check-N service and validate 2xx. Returns (success, stdout, stderr, error_msg)."""
    last_out, last_err = "", ""
    cid = command_id or check_name
    _emit(event_handler, "check_started", command_id=cid, url=url)
    for attempt in range(retry_attempts):
        if attempt > 0:
            time.sleep(retry_delay_s)
        try:
            out, err = compose_run(
                compose_path, check_name, verbose=verbose, no_deps=True
            )
            last_out, last_err = out, err
        except RuntimeError as exc:
            if attempt == retry_attempts - 1:
                return _finalize_check_failure(
                    event_handler,
                    command_id=cid,
                    url=url,
                    out=last_out,
                    err=last_err,
                    message=str(exc),
                )
            continue
        code_str, code, body_preview = _parse_http_check_output(out)
        if 200 <= code < 300:
            _emit(
                event_handler, "check_passed", command_id=cid, url=url, status_code=code
            )
            return (True, out, err, None)
        if attempt == retry_attempts - 1:
            msg = f"Check failed: {url!r} returned HTTP {code_str}{body_preview}"
            return _finalize_check_failure(
                event_handler,
                command_id=cid,
                url=url,
                out=out,
                err=err,
                message=msg,
            )
    err_msg = f"Check failed: {url!r} returned no valid HTTP status"
    return _finalize_check_failure(
        event_handler,
        command_id=cid,
        url=url,
        out=last_out,
        err=last_err,
        message=err_msg,
    )


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


def _collect_check_urls(scenario: ScenarioConfig) -> list[str]:
    """Resolve commands and return URLs for http_check commands in order."""
    facts = _facts_by_name(scenario)
    urls: list[str] = []
    for cmd in scenario.commands:
        if not cmd.run.argv:
            continue
        first = cmd.run.argv[0]
        if first != "http_check":
            continue
        resolved = resolve_argv(cmd.run.argv, facts)
        if len(resolved) >= 2:
            urls.append(resolved[1])
    return urls


def _artifact_map(topology: TopologyConfig) -> dict[str, TopologyArtifactDef]:
    return {artifact.id: artifact for artifact in topology.artifacts}


def _collect_artifact_refs_from_argv(argv: list[str | object]) -> set[str]:
    refs: set[str] = set()
    for item in argv:
        if isinstance(item, ArgvArtifactRef):
            refs.add(item.artifact)
    return refs


def _validate_artifact_grants_against_topology(
    scenario: ScenarioConfig,
    topology: TopologyConfig,
) -> None:
    """Validate scenario artifact grants and command artifact refs against topology artifacts."""
    artifacts = _artifact_map(topology)
    node_by_id = {node.id: node for node in scenario.nodes}

    for node in scenario.nodes:
        for artifact_id in node.artifacts:
            if artifact_id not in artifacts:
                raise ValueError(
                    f"Scenario node {node.id!r} declares unknown artifact {artifact_id!r}. "
                    "Declare it in topology.yaml artifacts[].id."
                )

    for cmd in scenario.commands:
        refs = _collect_artifact_refs_from_argv(cmd.run.argv)
        if not refs:
            continue
        if cmd.node not in node_by_id:
            continue
        granted = set(node_by_id[cmd.node].artifacts)
        for artifact_id in refs:
            if artifact_id not in artifacts:
                raise ValueError(
                    f"Command {cmd.id!r} references unknown artifact {artifact_id!r}. "
                    "Declare it in topology.yaml artifacts[].id."
                )
            if artifact_id not in granted:
                raise ValueError(
                    f"Command {cmd.id!r} on node {cmd.node!r} references artifact "
                    f"{artifact_id!r} but it is not granted in nodes[].artifacts."
                )


def _topology_id_from_topology_file(topology_file: str) -> str | None:
    """Extract topology id from path like topologies/<topology-id>/topology.yaml."""
    parts = Path(topology_file).parts
    if len(parts) >= 3 and parts[0] == "topologies" and parts[-1] == "topology.yaml":
        return parts[1]
    return None


def _iter_required_artifact_ids(scenario: ScenarioConfig) -> list[str]:
    """Return unique artifact IDs referenced by command argv entries."""
    artifact_ids: list[str] = []
    seen: set[str] = set()
    for cmd in scenario.commands:
        for item in cmd.run.argv:
            if not isinstance(item, ArgvArtifactRef):
                continue
            if item.artifact in seen:
                continue
            seen.add(item.artifact)
            artifact_ids.append(item.artifact)
    return artifact_ids


def _stage_required_artifacts(
    scenario: ScenarioConfig,
    topology: TopologyConfig,
    project_root: Path,
) -> None:
    """Stage required artifact files into var/artifacts, preferring topology fixtures."""
    required_artifact_ids = _iter_required_artifact_ids(scenario)
    if not required_artifact_ids:
        return
    artifacts = _artifact_map(topology)
    topology_id = _topology_id_from_topology_file(scenario.topology)
    fixture_root = (
        project_root / "topologies" / topology_id / "fixtures" / "pcap"
        if topology_id
        else None
    )
    cache_root = project_root / RUNTIME_ARTIFACT_DIR
    cache_root.mkdir(parents=True, exist_ok=True)

    for artifact_id in required_artifact_ids:
        if artifact_id not in artifacts:
            raise RuntimeError(
                f"Missing required artifact id {artifact_id!r}. "
                "Declare it in topology.yaml artifacts[].id."
            )
        filename = artifacts[artifact_id].filename
        cache_path = cache_root / filename
        fixture_path = fixture_root / filename if fixture_root else None
        if fixture_path is not None and fixture_path.is_file():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            if not cache_path.exists() or not cmp(
                fixture_path, cache_path, shallow=False
            ):
                shutil.copy2(fixture_path, cache_path)
            continue
        if cache_path.is_file():
            continue
        expected_fixture = (
            str(fixture_path)
            if fixture_path is not None
            else "<topologies/<topology-id>/fixtures/pcap/...>"
        )
        raise RuntimeError(
            "Missing required artifact for scenario command argv:\n"
            f"  artifact id: {artifact_id}\n"
            f"  runtime path: /opt/artifacts/{filename}\n"
            f"Expected one of:\n"
            f"  - {expected_fixture}\n"
            f"  - {cache_path}\n"
            "Fix:\n"
            "  - add the file under topology fixtures, or\n"
            "  - run `make pull` to populate var/artifacts"
        )


def _execute_commands(
    scenario: ScenarioConfig,
    topology: TopologyConfig,
    facts: dict,
    compose_path: Path,
    verbose: bool,
    event_handler: EventHandler | None = None,
) -> tuple[list[CommandOutputItem], RuntimeError | None]:
    """Run all scenario commands in order. Returns (command_outputs, error)."""
    command_outputs: list[CommandOutputItem] = []
    check_index = 0
    build_by_node = {node.id: node.build for node in scenario.nodes}
    artifact_by_id = _artifact_map(topology)
    node_artifacts = {node.id: set(node.artifacts) for node in scenario.nodes}
    captured_facts: dict[str, str] = {}
    captured_env: dict[str, str] = {}
    for cmd in scenario.commands:
        effective_facts = dict(facts)
        effective_facts.update(
            {k: FactDef(name=k, value=v) for k, v in captured_facts.items()}
        )
        resolved = resolve_argv(
            cmd.run.argv,
            effective_facts,
            artifacts_by_id=artifact_by_id,
            allowed_artifact_ids=node_artifacts.get(cmd.node, set()),
            command_id=cmd.id,
            node_id=cmd.node,
        )
        if len(resolved) >= 2 and resolved[0] == "http_check":
            url = resolved[1]
            check_name = f"check-{check_index}"
            check_index += 1
            ok, out, err, error_msg = _run_check_service(
                compose_path,
                check_name,
                url,
                verbose,
                retry_attempts=cmd.retry.attempts,
                retry_delay_s=cmd.retry.delay_s,
                event_handler=event_handler,
                command_id=cmd.id,
            )
            command_outputs.append((cmd.id, resolved, out, err))
            if not ok and error_msg:
                return (command_outputs, RuntimeError(error_msg))
        else:
            use_exec = build_by_node.get(cmd.node) == "up"
            ok, out, err, error_msg = _run_command_with_retry(
                compose_path,
                cmd.id,
                cmd.node,
                cmd.retry.attempts,
                cmd.retry.delay_s,
                verbose,
                no_deps=False,
                command_argv=resolved,
                use_exec=use_exec,
                detached=cmd.run.detached and use_exec,
                env_override=captured_env if not use_exec else None,
                event_handler=event_handler,
            )
            command_outputs.append((cmd.id, resolved, out, err))
            if not ok and error_msg:
                return (command_outputs, RuntimeError(error_msg))
        if cmd.capture:
            text = out if cmd.capture.source == "stdout" else err
            match = re.search(cmd.capture.regex, text)
            if not match:
                return (
                    command_outputs,
                    RuntimeError(
                        f"capture regex did not match command output for command {cmd.id!r}"
                    ),
                )
            value = match.group(1).strip()
            captured_facts[cmd.capture.as_] = value
            if cmd.capture.env_var:
                captured_env[cmd.capture.env_var] = value
        if cmd.wait_s is not None and cmd.wait_s > 0:
            _emit(event_handler, "wait_started", command_id=cmd.id, wait_s=cmd.wait_s)
            time.sleep(cmd.wait_s)
            _emit(event_handler, "wait_completed", command_id=cmd.id, wait_s=cmd.wait_s)
    return (command_outputs, None)


def _build_success_result(
    scenario_name: str,
    duration_s: float,
    up_node_ids: list[str],
    command_outputs: list[CommandOutputItem],
    compose_up_stdout: str,
    compose_up_stderr: str,
    output_files: list[tuple[str, str]],
    compose_up_service_logs: list[tuple[str, str]] | None,
    verbose: bool,
) -> RunResult:
    """Build RunResult for successful run."""
    return RunResult(
        scenario=scenario_name,
        passed=True,
        duration_s=duration_s,
        up_node_ids=up_node_ids,
        command_outputs=command_outputs,
        compose_up_stdout=compose_up_stdout if verbose else None,
        compose_up_stderr=compose_up_stderr if verbose else None,
        output_files=output_files if verbose else None,
        compose_up_service_logs=compose_up_service_logs,
    )


def _persist_service_logs(
    scenario_name: str, service_logs: list[tuple[str, str]], base_dir: Path
) -> None:
    """Write per-service logs to ./var/log/<scenario_name>/<service>.log."""
    if not service_logs:
        return
    log_root = ensure_log_dir(base_dir / "var" / "log" / scenario_name)
    for service, logs in service_logs:
        path = log_root / f"{service}.log"
        try:
            path.write_text(logs or "", encoding="utf-8")
        except OSError as e:
            logger.debug("Could not write service log %s: %s", path, e)


def _run_compose_and_capture(
    compose_path: Path,
    scenario_name: str,
    scenario_dir: Path,
    scenario: ScenarioConfig,
    topology: TopologyConfig,
    verbose: bool,
    project_root: Path,
    event_handler: EventHandler | None = None,
) -> RunResult:
    """Run compose up (up nodes), then commands in order; return RunResult. Does not teardown."""
    start = time.perf_counter()
    up_node_ids = [n.id for n in scenario.nodes if n.build == "up"]
    try:
        facts = _facts_by_name(scenario)
        logger.debug("Compose up services: %s", up_node_ids)
        _emit(event_handler, "compose_up_started", services=up_node_ids)
        compose_up_stdout, compose_up_stderr = compose_up(
            compose_path, verbose=verbose, services=up_node_ids
        )
        for node_id in up_node_ids:
            _emit(event_handler, "service_up", node_id=node_id)
        command_outputs, cmd_error = _execute_commands(
            scenario,
            topology,
            facts,
            compose_path,
            verbose,
            event_handler=event_handler,
        )
        if cmd_error is not None:
            return RunResult(
                scenario=scenario_name,
                passed=False,
                duration_s=time.perf_counter() - start,
                error=str(cmd_error),
                up_node_ids=up_node_ids,
                command_outputs=command_outputs,
            )
        compose_up_service_logs = (
            compose_logs(compose_path, up_node_ids) if up_node_ids else None
        )
        if compose_up_service_logs:
            _persist_service_logs(scenario_name, compose_up_service_logs, project_root)
        output_dir = scenario_dir / ".build" / "output"
        output_files = _gather_output_files(output_dir) if verbose else []
        return _build_success_result(
            scenario_name,
            time.perf_counter() - start,
            up_node_ids,
            command_outputs,
            compose_up_stdout,
            compose_up_stderr,
            output_files,
            compose_up_service_logs if verbose else None,
            verbose,
        )
    except Exception as exc:
        logger.error("Scenario run failed: %s", exc)
        return RunResult(
            scenario=scenario_name,
            passed=False,
            duration_s=time.perf_counter() - start,
            error=str(exc),
            up_node_ids=up_node_ids,
            command_outputs=[],
        )


def _validate_scenario_against_topology(
    scenario: ScenarioConfig, topology: TopologyConfig
) -> None:
    """Validate scenario node ids and span targets exist in topology."""
    top_node_names = {n.name for n in topology.nodes}
    top_network_names = {n.name for n in topology.networks}
    for sn in scenario.nodes:
        if sn.id not in top_node_names:
            raise ValueError(
                f"Scenario node id {sn.id!r} is not in topology. "
                f"Valid nodes: {sorted(top_node_names)}"
            )
        if sn.span is not None and sn.span not in top_node_names:
            raise ValueError(
                f"Scenario node {sn.id!r} span target {sn.span!r} is not in topology. "
                f"Valid nodes: {sorted(top_node_names)}"
            )
        if sn.networks is not None:
            for net in sn.networks:
                if net not in top_network_names:
                    raise ValueError(
                        f"Scenario node {sn.id!r} network {net!r} is not in topology. "
                        f"Valid networks: {sorted(top_network_names)}"
                    )


def run_scenario(
    scenario_dir: Path,
    *,
    keep: bool = False,
    verbose: bool = False,
    project_root: Path | None = None,
    event_handler: EventHandler | None = None,
) -> RunResult:
    """Run a scenario: parse, generate compose, up, teardown (unless --keep)."""
    base = project_root or Path.cwd()
    scenario = load_scenario(scenario_dir)
    topology = load_topology(scenario.topology, base_dir=base)

    _validate_artifact_grants_against_topology(scenario, topology)
    _stage_required_artifacts(scenario, topology, base)

    node_names = [n.id for n in scenario.nodes]
    topology = _filter_topology(topology, node_names)
    _validate_scenario_against_topology(scenario, topology)
    facts = _facts_by_name(scenario)

    check_urls = _collect_check_urls(scenario)
    build_dir = scenario_dir / ".build"
    (build_dir / "output").mkdir(parents=True, exist_ok=True)
    logger.debug("Generating compose in %s", build_dir)
    compose_path = generate_compose(
        topology,
        build_dir,
        verbose=verbose,
        scenario_nodes=scenario.nodes,
        scenario_facts=scenario.facts,
        check_urls=check_urls,
    )
    result = _run_compose_and_capture(
        compose_path,
        scenario.name,
        scenario_dir,
        scenario,
        topology,
        verbose,
        base,
        event_handler=event_handler,
    )

    if not keep:
        try:
            logger.debug("Compose down: %s", compose_path)
            compose_down(compose_path)
        except Exception:
            pass

    return result
