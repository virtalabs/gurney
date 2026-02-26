"""Docker Compose generation and lifecycle management."""

import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import yaml

from testbed.models import (
    FactDef,
    NodeDef,
    ScenarioNodeDef,
    TopologyConfig,
)

logger = logging.getLogger(__name__)

# Optional callback (line, stream_name) for streaming output. stream_name is "stdout" or "stderr".
StreamLineHandler = Callable[[str, str], None]


def _run_with_optional_stream(
    cmd: list[str],
    on_line: StreamLineHandler | None,
) -> tuple[str, str, int]:
    """Run command; if on_line is set, call it for each line of stdout/stderr. Returns (stdout, stderr, returncode)."""
    if on_line is None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return (result.stdout or "", result.stderr or "", result.returncode)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        if proc.stdout is None:
            return
        for line in iter(proc.stdout.readline, ""):
            stdout_lines.append(line)
            on_line(line, "stdout")

    def read_stderr() -> None:
        if proc.stderr is None:
            return
        for line in iter(proc.stderr.readline, ""):
            stderr_lines.append(line)
            on_line(line, "stderr")

    t_out = threading.Thread(target=read_stdout)
    t_err = threading.Thread(target=read_stderr)
    t_out.start()
    t_err.start()
    t_out.join()
    t_err.join()
    proc.wait()
    return ("".join(stdout_lines), "".join(stderr_lines), proc.returncode or 0)


CHECK_IMAGE = "curlimages/curl"
# Use -s -w "\n%{http_code}" (no -f) so status can be validated by runner.
CHECK_CURL_ARGS = [
    "curl",
    "-s",
    "-w",
    "\n%{http_code}",
    "--retry",
    "3",
    "--retry-delay",
    "2",
]


def _build_networks(topology: TopologyConfig) -> dict:
    """Build networks dict for docker-compose."""
    return {
        net.name: {
            "driver": "bridge",
            "ipam": {
                "driver": "default",
                "config": [{"subnet": net.cidr}],
            },
            "labels": {"testbed.managed": "true"},
        }
        for net in topology.networks
    }


def _build_depends_on(node: NodeDef, topology: TopologyConfig) -> dict | None:
    """Build depends_on dict for a service. Returns None if no dependencies."""
    if not node.depends_on:
        return None
    nodes_by_name = {n.name: n for n in topology.nodes}
    deps = {}
    for dep in node.depends_on:
        dep_node = nodes_by_name.get(dep)
        condition = (
            "service_healthy"
            if dep_node and dep_node.healthcheck
            else "service_started"
        )
        deps[dep] = {"condition": condition}
    return deps


def _apply_networking(
    service: dict,
    node: NodeDef,
    network_mode: str | None,
    networks_override: dict[str, dict] | None,
) -> None:
    """Set network_mode or networks on service dict."""
    if network_mode is not None:
        service["network_mode"] = network_mode
        return
    if networks_override is not None:
        service["networks"] = networks_override
        return
    network_config: dict = {}
    if node.ip is not None:
        network_config["ipv4_address"] = node.ip
    service["networks"] = (
        {node.network: network_config} if network_config else {node.network: {}}
    )


def _merge_env_into_service(
    service: dict,
    node: NodeDef,
    environment_override: dict[str, str] | None,
) -> None:
    """Set service['environment'] from node env and override."""
    env = dict(node.environment)
    if environment_override:
        env.update(environment_override)
    if env:
        service["environment"] = [f"{k}={v}" for k, v in env.items()]


def _apply_healthcheck(service: dict, node: NodeDef) -> None:
    """Set service healthcheck from node if present."""
    if not node.healthcheck:
        return
    hc = node.healthcheck
    service["healthcheck"] = {
        "test": hc.test,
        "interval": f"{hc.interval_s}s",
        "timeout": f"{hc.timeout_s}s",
        "retries": hc.retries,
    }


def _apply_optional_service_fields(
    service: dict,
    node: NodeDef,
    topology: TopologyConfig,
    environment_override: dict[str, str] | None,
) -> None:
    """Set ports, env, command, volumes, healthcheck, depends_on, cap_add if present."""
    if node.ports:
        service["ports"] = [f"{p}:{p}" for p in node.ports]
    _merge_env_into_service(service, node, environment_override)
    if node.command:
        service["command"] = node.command.split()
    if node.volumes:
        service["volumes"] = node.volumes
    _apply_healthcheck(service, node)
    deps = _build_depends_on(node, topology)
    if deps:
        service["depends_on"] = deps
    if node.cap_add:
        service["cap_add"] = node.cap_add


def _build_service(
    node: NodeDef,
    topology: TopologyConfig,
    verbose: bool = False,
    network_mode: str | None = None,
    environment_override: dict[str, str] | None = None,
    networks_override: dict[str, dict] | None = None,
) -> dict:
    """Build service dict for a single node."""
    service: dict = {
        "image": _expand_image(node.image),
        "labels": {"testbed.managed": "true"},
    }
    _apply_networking(service, node, network_mode, networks_override)
    _apply_optional_service_fields(service, node, topology, environment_override)
    return service


def _compose_ps_snapshot(compose_path: Path) -> dict:
    """Best-effort service state snapshot for debugging."""
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "ps",
            "--all",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {"ps_error": (proc.stderr or proc.stdout or "").strip()[:400]}
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"services": []}
    services = _parse_ps_json_array(raw)
    if services is None:
        services = _parse_ps_json_lines(raw)
    return {"services": services}


def _normalize_ps_item(item: dict[str, object]) -> dict[str, object]:
    """Map docker compose ps item keys to compact snapshot schema."""
    return {
        "service": item.get("Service"),
        "state": item.get("State"),
        "health": item.get("Health"),
        "exit_code": item.get("ExitCode"),
    }


def _parse_ps_json_array(raw: str) -> list[dict[str, object]] | None:
    """Parse array-shaped JSON output from compose ps; returns None when not array JSON."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [_normalize_ps_item(item) for item in parsed if isinstance(item, dict)]


def _parse_ps_json_lines(raw: str) -> list[dict[str, object]]:
    """Parse line-delimited JSON records from compose ps output."""
    services: list[dict[str, object]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            services.append(_normalize_ps_item(item))
    return services


def _service_logs_snapshot(compose_path: Path, service: str, tail: int = 120) -> str:
    """Best-effort compose logs snapshot for one service."""
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "logs",
            "--no-log-prefix",
            f"--tail={tail}",
            service,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "").strip()[:600]
    return (proc.stdout or "").strip()[:2000]


def _service_ip_snapshot(compose_path: Path, service: str) -> str:
    """Best-effort runtime container IP lookup for a compose service."""
    ps = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "ps", "-q", service],
        capture_output=True,
        text=True,
    )
    cid = (ps.stdout or "").strip().splitlines()
    if ps.returncode != 0 or not cid:
        return ""
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            cid[0],
        ],
        capture_output=True,
        text=True,
    )
    if inspect.returncode != 0:
        return ""
    return (inspect.stdout or "").strip()


def _build_check_services(check_urls: list[str], topology: TopologyConfig) -> dict:
    """Build synthetic check-N services for each URL. Validates host matches a node."""
    nodes_by_name = {n.name: n for n in topology.nodes}
    services = {}
    for i, url in enumerate(check_urls):
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"Check URL has no host: {url!r}")
        if hostname not in nodes_by_name:
            raise ValueError(
                f"Check URL host {hostname!r} must match a topology node. "
                f"Valid nodes: {sorted(nodes_by_name)}"
            )
        node = nodes_by_name[hostname]
        condition = "service_healthy" if node.healthcheck else "service_started"
        service = {
            "image": CHECK_IMAGE,
            "labels": {"testbed.managed": "true"},
            "command": CHECK_CURL_ARGS + [url],
            "depends_on": {hostname: {"condition": condition}},
            "networks": {node.network: {}},
        }
        services[f"check-{i}"] = service
    return services


def _env_overrides_from_scenario(
    scenario_nodes: list[ScenarioNodeDef],
    scenario_facts: list[FactDef],
) -> dict[str, dict[str, str]]:
    """Build per-node env overrides from scenario nodes' env_from_fact."""
    facts_by_name = {f.name: f for f in scenario_facts}
    overrides: dict[str, dict[str, str]] = {}
    for sn in scenario_nodes:
        if sn.env_from_fact is None:
            continue
        if sn.env_from_fact not in facts_by_name:
            continue
        fact = facts_by_name[sn.env_from_fact]
        if isinstance(fact.value, dict):
            overrides[sn.id] = {k: str(v) for k, v in fact.value.items()}
    return overrides


def _build_service_for_node(
    n: NodeDef,
    topology: TopologyConfig,
    verbose: bool,
    scenario_node: ScenarioNodeDef | None,
    node_env_override: dict[str, str] | None,
    listener: str | None,
) -> dict:
    """Build one service dict for a topology node."""
    if listener is not None:
        return _build_service(
            n,
            topology,
            verbose,
            network_mode=f"service:{listener}",
            environment_override=node_env_override,
        )
    if scenario_node is not None and scenario_node.networks is not None:
        networks_override = {net: {} for net in scenario_node.networks}
        # Preserve static IP when scenario narrows/overrides network attachments.
        if n.ip is not None and n.network in networks_override:
            networks_override[n.network] = {"ipv4_address": n.ip}
        return _build_service(
            n,
            topology,
            verbose,
            environment_override=node_env_override,
            networks_override=networks_override,
        )
    return _build_service(n, topology, verbose, environment_override=node_env_override)


def generate_compose(
    topology: TopologyConfig,
    output_dir: Path,
    verbose: bool = False,
    scenario_nodes: list[ScenarioNodeDef] | None = None,
    scenario_facts: list[FactDef] | None = None,
    check_urls: list[str] | None = None,
) -> Path:
    """Render docker-compose.yaml from topology and write to output_dir. Returns path to file.
    scenario_nodes: ordered node defs; span and env_from_fact drive network_mode and env.
    scenario_facts: used to resolve env_from_fact map values.
    check_urls: URLs for synthetic check-N services (http_check commands)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    compose_path = output_dir / "docker-compose.yaml"

    nodes = scenario_nodes or []
    facts = scenario_facts or []
    scenario_node_by_id = {sn.id: sn for sn in nodes}
    sender_to_listener = {sn.id: sn.span for sn in nodes if sn.span is not None}
    overrides = _env_overrides_from_scenario(nodes, facts)

    networks = _build_networks(topology)
    services = {}
    for n in topology.nodes:
        sn = scenario_node_by_id.get(n.name)
        node_env_override = overrides.get(n.name)
        listener = sender_to_listener.get(n.name)
        services[n.name] = _build_service_for_node(
            n, topology, verbose, sn, node_env_override, listener
        )

    if check_urls:
        services.update(_build_check_services(check_urls, topology))

    # Use default_style to avoid long command strings being folded;
    # "-flag" at line start would be parsed as YAML list element
    with open(compose_path, "w") as f:
        yaml.dump(
            {"services": services, "networks": networks},
            f,
            default_flow_style=False,
            sort_keys=False,
            default_style='"',
        )

    logger.debug("Wrote compose file: %s", compose_path)
    return compose_path


def _expand_image(image: str) -> str:
    """Expand ${VAR:-default} in image names using os.environ."""

    def replacer(match: re.Match) -> str:
        var, default = match.group(1), match.group(2)
        return os.environ.get(var, default)

    return re.sub(r"\$\{([^:}]+):-([^}]*)\}", replacer, image)


def _build_compose_run_cmd(
    compose_path: Path,
    service: str,
    no_deps: bool = False,
    command: list[str] | None = None,
) -> list[str]:
    """Build `docker compose run` command for one-off service execution."""
    run_cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        "run",
        "--rm",
        "--quiet-pull",
    ]
    if no_deps:
        run_cmd.append("--no-deps")
    if command:
        run_cmd.extend(["--entrypoint", ""])
    run_cmd.append(service)
    if command:
        run_cmd.extend(command)
    return run_cmd


def _collect_run_failure_context(
    compose_path: Path, service: str, stdout: str, stderr: str
) -> dict[str, object]:
    """Collect targeted debugging context for failed compose runs."""
    context: dict[str, object] = {}
    if service == "replay":
        context["replay_failure"] = {
            "stderr_full": stderr[:2000],
            "stdout_full": stdout[:2000],
        }
    if service.startswith("check-"):
        context["snapshot"] = _compose_ps_snapshot(compose_path)
        context["blueflow_logs"] = _service_logs_snapshot(compose_path, "blueflow-api")
        context["ip_snapshot"] = {
            "postgres": _service_ip_snapshot(compose_path, "postgres"),
            "blueflow_api": _service_ip_snapshot(compose_path, "blueflow-api"),
        }
    return context


def _compose_run_error_message(stdout: str, stderr: str, returncode: int) -> str:
    """Build normalized compose run error message from command output."""
    parts = [s.strip() for s in (stderr, stdout) if s.strip()]
    msg = "\n".join(parts) if parts else "unknown error"
    if returncode == 22:
        msg += " Curl exit 22 = HTTP 4xx/5xx (check endpoint and server)."
    return msg


def compose_up(
    compose_path: Path,
    verbose: bool = False,
    services: list[str] | None = None,
    on_line: StreamLineHandler | None = None,
) -> tuple[str, str]:
    """Run docker compose up -d --wait. If services given, only start those.
    Always captures output; returns (stdout, stderr). Optional on_line called per line when set."""
    cmd = ["docker", "compose", "-f", str(compose_path), "up", "-d", "--wait"]
    if services:
        cmd.extend(services)
    stdout, stderr, returncode = _run_with_optional_stream(cmd, on_line)
    if returncode != 0:
        msg = (stderr or stdout or "unknown error")[:500]
        logger.error("docker compose up failed: %s", msg)
        raise RuntimeError(
            f"docker compose up failed: {stderr or stdout or 'unknown error'}"
        )
    logger.debug("Compose up succeeded: %s", compose_path)
    return (stdout, stderr)


def compose_run(
    compose_path: Path,
    service: str,
    verbose: bool = False,
    no_deps: bool = False,
    command: list[str] | None = None,
    on_line: StreamLineHandler | None = None,
) -> tuple[str, str]:
    """Run a service as one-off (docker compose run --rm --quiet-pull).
    Always captures output; returns (stdout, stderr). Optional on_line called per line when set.
    no_deps: add --no-deps so Compose does not start/wait for dependencies (reduces stderr noise for checks).
    command: optional argv to run instead of service default (appended after service name).
    """
    run_cmd = _build_compose_run_cmd(
        compose_path, service, no_deps=no_deps, command=command
    )
    stdout, stderr, returncode = _run_with_optional_stream(run_cmd, on_line)
    if returncode != 0:
        context = _collect_run_failure_context(compose_path, service, stdout, stderr)
        msg = _compose_run_error_message(stdout, stderr, returncode)
        if context:
            logger.debug("compose run failure context for %s: %s", service, context)
        logger.error("docker compose run %s failed: %s", service, msg[:500])
        raise RuntimeError(f"docker compose run {service} failed: {msg}")
    logger.debug("Compose run %s succeeded", service)
    return (stdout, stderr)


def compose_exec(
    compose_path: Path,
    service: str,
    command: list[str],
    detached: bool = False,
    on_line: StreamLineHandler | None = None,
) -> tuple[str, str]:
    """Run a command in an existing service container via docker compose exec.
    Optional on_line called per line when set."""
    exec_cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        "exec",
        "-T",
    ]
    if detached:
        exec_cmd.append("-d")
    exec_cmd.append(service)
    exec_cmd += command
    stdout, stderr, returncode = _run_with_optional_stream(exec_cmd, on_line)
    if returncode != 0:
        parts = [s.strip() for s in (stderr, stdout) if s.strip()]
        msg = "\n".join(parts) if parts else "unknown error"
        logger.error("docker compose exec %s failed: %s", service, msg[:500])
        raise RuntimeError(f"docker compose exec {service} failed: {msg}")
    logger.debug("Compose exec %s succeeded", service)
    return (stdout, stderr)


def compose_logs(compose_path: Path, services: list[str]) -> list[tuple[str, str]]:
    """Fetch logs for each service. Returns [(service_name, logs), ...] in order.
    Does not raise on non-zero (e.g. exited containers); returns whatever was captured.
    """
    logger.debug("Fetching compose logs for %s", services)
    result: list[tuple[str, str]] = []
    for service in services:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_path),
                "logs",
                "--no-log-prefix",
                service,
            ],
            capture_output=True,
            text=True,
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        logs = (out + ("\n" + err if err else "")).strip()
        result.append((service, logs))
    return result


def compose_down(compose_path: Path) -> None:
    """Run docker compose down -v --remove-orphans."""
    logger.debug("Compose down: %s", compose_path)
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "down",
            "-v",
            "--remove-orphans",
        ],
        capture_output=True,
        check=True,
    )


def force_cleanup() -> None:
    """Remove all containers and networks with testbed.managed=true label."""
    logger.debug("Force cleanup: removing testbed-managed containers and networks")
    # Stop and remove containers
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=testbed.managed=true"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        for cid in result.stdout.strip().split("\n"):
            subprocess.run(
                ["docker", "rm", "-f", cid], capture_output=True, check=False
            )

    # Remove networks
    result = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", "label=testbed.managed=true"],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        for nid in result.stdout.strip().split("\n"):
            subprocess.run(
                ["docker", "network", "rm", nid], capture_output=True, check=False
            )
