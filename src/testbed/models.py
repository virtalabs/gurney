"""Pydantic models for topology and scenario configuration."""

import ipaddress
from typing import Literal

from pydantic import BaseModel, model_validator


class HealthCheck(BaseModel):
    """Docker health check configuration."""

    test: list[str]
    interval_s: int = 10
    timeout_s: int = 5
    retries: int = 3


class NetworkDef(BaseModel):
    """Network definition in the topology."""

    name: str
    cidr: str
    vlan_id: int | None = None


class NodeDef(BaseModel):
    """Docker node definition in the topology."""

    name: str
    kind: Literal["docker"]
    image: str
    network: str
    ip: str
    ports: list[int] = []
    environment: dict[str, str] = {}
    command: str | None = None
    depends_on: list[str] = []
    healthcheck: HealthCheck | None = None
    volumes: list[str] = []


def _validate_unique_node_names(nodes: list[NodeDef]) -> None:
    if len({n.name for n in nodes}) != len(nodes):
        raise ValueError("Duplicate node names found")


def _validate_node_network_ref(node: NodeDef, network_names: set[str]) -> None:
    if node.network not in network_names:
        raise ValueError(
            f"Node '{node.name}' references unknown network '{node.network}'"
        )


def _validate_node_depends_on(node: NodeDef, node_names: set[str]) -> None:
    for dep in node.depends_on:
        if dep not in node_names:
            raise ValueError(f"Node '{node.name}' depends on unknown node '{dep}'")
        if dep == node.name:
            raise ValueError(f"Node '{node.name}' cannot depend on itself")


def _kahn_process_node(
    node_name: str,
    dependents: dict[str, list[str]],
    in_degree: dict[str, int],
    queue: list[str],
) -> None:
    # Decrement in-degree of each dependent; enqueue any that reach zero.
    for v in dependents[node_name]:
        in_degree[v] -= 1
        if in_degree[v] == 0:
            queue.append(v)


def _validate_no_circular_deps(nodes: list[NodeDef]) -> None:
    # Raise ValueError if depends_on contains a cycle (Kahn's algorithm).
    in_degree = {n.name: len(n.depends_on) for n in nodes}
    dependents = {
        n.name: [m.name for m in nodes if n.name in m.depends_on] for n in nodes
    }
    queue = [n for n, d in in_degree.items() if d == 0]
    seen = 0
    while queue:
        u = queue.pop(0)
        seen += 1
        _kahn_process_node(u, dependents, in_degree, queue)
    if seen != len(nodes):
        raise ValueError("Circular dependency in depends_on")


def _validate_node_ip_in_cidr(
    node: NodeDef, network_by_name: dict[str, NetworkDef]
) -> None:
    net_def = network_by_name[node.network]
    network = ipaddress.ip_network(net_def.cidr, strict=False)
    try:
        ip = ipaddress.ip_address(node.ip)
    except ValueError:
        raise ValueError(f"Node '{node.name}' has invalid IP '{node.ip}'") from None
    if ip not in network:
        raise ValueError(
            f"Node '{node.name}' IP {node.ip} is not in network {net_def.cidr}"
        )


def _validate_no_duplicate_ips(nodes: list[NodeDef]) -> None:
    ips = [n.ip for n in nodes]
    if len(ips) != len(set(ips)):
        raise ValueError("Duplicate IP addresses found")


class TopologyConfig(BaseModel):
    """Full topology configuration from testbed.yaml."""

    networks: list[NetworkDef]
    nodes: list[NodeDef]

    @model_validator(mode="after")
    def validate_topology(self) -> "TopologyConfig":
        """Validate IPs, unique names, network refs, depends_on refs, no cycles."""
        network_names = {n.name for n in self.networks}
        node_names = {n.name for n in self.nodes}
        network_by_name = {n.name: n for n in self.networks}

        _validate_unique_node_names(self.nodes)
        _validate_no_duplicate_ips(self.nodes)
        _validate_no_circular_deps(self.nodes)

        for node in self.nodes:
            _validate_node_network_ref(node, network_names)
            _validate_node_depends_on(node, node_names)
            _validate_node_ip_in_cidr(node, network_by_name)

        return self


class ScenarioConfig(BaseModel):
    """Scenario configuration from scenario.yaml."""

    name: str
    topology: str = "testbed.yaml"
    nodes: list[str] | None = None  # None = all nodes
