"""Docker Compose generation and lifecycle management."""

import os
import re
import subprocess
from pathlib import Path

import yaml

from testbed.models import NodeDef, TopologyConfig


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
        condition = "service_healthy" if dep_node and dep_node.healthcheck else "service_started"
        deps[dep] = {"condition": condition}
    return deps


def _build_service(node: NodeDef, topology: TopologyConfig) -> dict:
    """Build service dict for a single node."""
    service: dict = {
        "image": _expand_image(node.image),
        "networks": {node.network: {"ipv4_address": node.ip}},
        "labels": {"testbed.managed": "true"},
    }

    if node.ports:
        service["ports"] = [f"{p}:{p}" for p in node.ports]

    if node.environment:
        service["environment"] = [f"{k}={v}" for k, v in node.environment.items()]

    if node.command:
        service["command"] = node.command

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

    return service


def generate_compose(topology: TopologyConfig, output_dir: Path) -> Path:
    """Render docker-compose.yaml from topology and write to output_dir. Returns path to file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    compose_path = output_dir / "docker-compose.yaml"

    networks = _build_networks(topology)
    services = {n.name: _build_service(n, topology) for n in topology.nodes}

    with open(compose_path, "w") as f:
        yaml.dump(
            {"services": services, "networks": networks},
            f,
            default_flow_style=False,
            sort_keys=False,
        )

    return compose_path


def _expand_image(image: str) -> str:
    """Expand ${VAR:-default} in image names using os.environ."""

    def replacer(match: re.Match) -> str:
        var, default = match.group(1), match.group(2)
        return os.environ.get(var, default)

    return re.sub(r"\$\{([^:}]+):-([^}]*)\}", replacer, image)


def compose_up(compose_path: Path, verbose: bool = False) -> None:
    """Run docker compose up -d --wait."""
    cmd = ["docker", "compose", "-f", str(compose_path), "up", "-d", "--wait"]
    result = subprocess.run(
        cmd,
        capture_output=not verbose,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose up failed: {result.stderr or result.stdout or 'unknown error'}"
        )


def compose_down(compose_path: Path) -> None:
    """Run docker compose down -v --remove-orphans."""
    subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "down", "-v", "--remove-orphans"],
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
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, check=False)

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
