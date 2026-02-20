"""Docker Compose generation and lifecycle management."""

import os
import re
import subprocess
from pathlib import Path

import yaml

from testbed.models import NodeDef, SpanConfig, TopologyConfig


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


def _build_service(
    node: NodeDef,
    topology: TopologyConfig,
    verbose: bool = False,
    network_mode: str | None = None,
) -> dict:
    """Build service dict for a single node."""
    service: dict = {
        "image": _expand_image(node.image),
        "labels": {"testbed.managed": "true"},
    }
    if network_mode is not None:
        service["network_mode"] = network_mode
    else:
        network_config: dict = {}
        if node.ip is not None:
            network_config["ipv4_address"] = node.ip
        service["networks"] = (
            {node.network: network_config} if network_config else {node.network: {}}
        )

    # When verbose: do NOT set tty/stdin_open for services. Containers with TTY
    # do not have their stdout/stderr captured by the log driver, so
    # "docker compose logs" returns empty and --verbose would show nothing.
    if node.ports:
        service["ports"] = [f"{p}:{p}" for p in node.ports]

    if node.environment:
        service["environment"] = [f"{k}={v}" for k, v in node.environment.items()]

    if node.command:
        service["command"] = node.command.split()

    if node.volumes:
        service["volumes"] = node.volumes

    if node.healthcheck:
        hc = node.healthcheck
        service["healthcheck"] = {
            "test": hc.test,
            "interval": f"{hc.interval_s}s",
            "timeout": f"{hc.timeout_s}s",
            "retries": hc.retries,
        }

    deps = _build_depends_on(node, topology)
    if deps:
        service["depends_on"] = deps

    if node.cap_add:
        service["cap_add"] = node.cap_add

    return service


def generate_compose(
    topology: TopologyConfig,
    output_dir: Path,
    verbose: bool = False,
    span: list[SpanConfig] | None = None,
) -> Path:
    """Render docker-compose.yaml from topology and write to output_dir. Returns path to file.
    verbose is used by the runner to decide whether to collect and show logs after the run.
    span: sender services get network_mode service:<listener> and no networks."""
    output_dir.mkdir(parents=True, exist_ok=True)
    compose_path = output_dir / "docker-compose.yaml"

    sender_to_listener = {s.sender: s.listener for s in (span or [])}
    networks = _build_networks(topology)
    services = {}
    for n in topology.nodes:
        listener = sender_to_listener.get(n.name)
        if listener is not None:
            services[n.name] = _build_service(
                n, topology, verbose, network_mode=f"service:{listener}"
            )
        else:
            services[n.name] = _build_service(n, topology, verbose)

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

    return compose_path


def _expand_image(image: str) -> str:
    """Expand ${VAR:-default} in image names using os.environ."""

    def replacer(match: re.Match) -> str:
        var, default = match.group(1), match.group(2)
        return os.environ.get(var, default)

    return re.sub(r"\$\{([^:}]+):-([^}]*)\}", replacer, image)


def compose_up(
    compose_path: Path, verbose: bool = False, services: list[str] | None = None
) -> tuple[str, str]:
    """Run docker compose up -d --wait. If services given, only start those.
    Always captures output; returns (stdout, stderr)."""
    cmd = ["docker", "compose", "-f", str(compose_path), "up", "-d", "--wait"]
    if services:
        cmd.extend(services)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose up failed: {stderr or stdout or 'unknown error'}"
        )
    return (stdout, stderr)


def compose_run(compose_path: Path, service: str, verbose: bool = False) -> tuple[str, str]:
    """Run a service as one-off (docker compose run --rm --quiet-pull).
    Always captures output; returns (stdout, stderr). Caller may print when verbose."""
    cmd = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        "run",
        "--rm",
        "--quiet-pull",
        service,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose run {service} failed: {stderr or stdout}"
        )
    return (stdout, stderr)


def compose_logs(compose_path: Path, services: list[str]) -> list[tuple[str, str]]:
    """Fetch logs for each service. Returns [(service_name, logs), ...] in order.
    Does not raise on non-zero (e.g. exited containers); returns whatever was captured."""
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
