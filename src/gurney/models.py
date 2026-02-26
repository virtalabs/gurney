"""Pydantic models for topology and scenario configuration."""

import ipaddress
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class HealthCheck(BaseModel):
    """Docker health check configuration."""

    model_config = ConfigDict(extra="forbid")

    test: list[str]
    interval_s: int = 10
    timeout_s: int = 5
    retries: int = 3


class NetworkDef(BaseModel):
    """Network definition in the topology."""

    model_config = ConfigDict(extra="forbid")

    name: str
    cidr: str
    vlan_id: int | None = None


class NodeDef(BaseModel):
    """Docker node definition in the topology."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["docker"]
    image: str
    network: str
    ip: str | None = None  # None = dynamic allocation (e.g. for compose run one-offs)
    ports: list[int] = []
    environment: dict[str, str] = {}
    command: str | None = None
    depends_on: list[str] = []
    healthcheck: HealthCheck | None = None
    volumes: list[str] = []
    cap_add: list[str] = []


class TopologyArtifactDef(BaseModel):
    """Topology-scoped artifact catalog entry."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["pcap"]
    filename: str
    url: str
    sha256: str | None = None


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
    if node.ip is None:
        return
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
    ips = [n.ip for n in nodes if n.ip is not None]
    if len(ips) != len(set(ips)):
        raise ValueError("Duplicate IP addresses found")


class TopologyConfig(BaseModel):
    """Full topology configuration from testbed.yaml."""

    model_config = ConfigDict(extra="forbid")

    networks: list[NetworkDef]
    artifacts: list[TopologyArtifactDef] = []
    nodes: list[NodeDef]

    @model_validator(mode="after")
    def validate_topology(self) -> "TopologyConfig":
        """Validate IPs, unique names, network refs, depends_on refs, no cycles."""
        network_names = {n.name for n in self.networks}
        node_names = {n.name for n in self.nodes}
        network_by_name = {n.name: n for n in self.networks}
        artifact_ids = [a.id for a in self.artifacts]

        _validate_unique_node_names(self.nodes)
        _validate_no_duplicate_ips(self.nodes)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifacts[].id must be unique")

        for node in self.nodes:
            _validate_node_network_ref(node, network_names)
            _validate_node_depends_on(node, node_names)
            _validate_node_ip_in_cidr(node, network_by_name)
        _validate_no_circular_deps(self.nodes)

        return self


class FactDef(BaseModel):
    """Global fact: name and value (scalar or map for env_from_fact)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str | dict[str, Any]


class ArgvFactRef(BaseModel):
    """Explicit fact reference in command argv: { fact: <fact-key> }."""

    model_config = ConfigDict(extra="forbid")

    fact: str


class ArgvArtifactRef(BaseModel):
    """Explicit artifact reference in command argv: { artifact: <artifact-id> }."""

    model_config = ConfigDict(extra="forbid")

    artifact: str


class ScenarioNodeDef(BaseModel):
    """Scenario node: ordered build step with network attachment or span."""

    model_config = ConfigDict(extra="forbid")

    id: str
    build: Literal["up", "run"]
    networks: list[str] | None = None
    span: str | None = None  # listener node id; mutually exclusive with networks
    env_from_fact: str | None = None
    artifacts: list[str] = []

    @model_validator(mode="after")
    def span_xor_networks(self) -> "ScenarioNodeDef":
        if self.span is not None and self.networks is not None:
            raise ValueError("node must not define both span and networks")
        return self


class RetryConfig(BaseModel):
    """Retry policy for a command."""

    model_config = ConfigDict(extra="forbid")

    attempts: int = 1
    delay_s: float = 0.0

    @model_validator(mode="after")
    def validate_bounds(self) -> "RetryConfig":
        if self.attempts < 1:
            raise ValueError("retry.attempts must be >= 1")
        if self.delay_s < 0:
            raise ValueError("retry.delay_s must be >= 0")
        return self


class CommandRunDef(BaseModel):
    """Command run: argv with optional { fact: key } items."""

    model_config = ConfigDict(extra="forbid")

    argv: list[str | ArgvFactRef | ArgvArtifactRef]
    detached: bool = False


class CommandDef(BaseModel):
    """Scenario command: bound to a node with retry policy."""

    model_config = ConfigDict(extra="forbid")

    id: str
    node: str
    run: CommandRunDef
    retry: RetryConfig


def _validate_scenario_fact_names_unique(facts: list[FactDef]) -> None:
    names = [f.name for f in facts]
    if len(names) != len(set(names)):
        raise ValueError("facts[].name must be unique")


def _validate_scenario_node_ids_unique(nodes: list[ScenarioNodeDef]) -> None:
    ids = [n.id for n in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("nodes[].id must be unique")


def _validate_scenario_command_ids_unique(commands: list[CommandDef]) -> None:
    ids = [c.id for c in commands]
    if len(ids) != len(set(ids)):
        raise ValueError("commands[].id must be unique")


def _validate_commands_reference_nodes(
    commands: list[CommandDef], node_ids: set[str]
) -> None:
    for c in commands:
        if c.node not in node_ids:
            raise ValueError(
                f"commands[].node must reference a declared node id: {c.node!r}"
            )


def _collect_fact_refs_from_argv(
    argv: list[str | ArgvFactRef | ArgvArtifactRef],
) -> set[str]:
    refs: set[str] = set()
    for item in argv:
        if isinstance(item, ArgvFactRef):
            refs.add(item.fact)
    return refs


def _validate_fact_refs_resolve(
    commands: list[CommandDef], fact_names: set[str]
) -> None:
    for c in commands:
        refs = _collect_fact_refs_from_argv(c.run.argv)
        missing = refs - fact_names
        if missing:
            raise ValueError(
                f"command {c.id!r} references unknown fact(s): {sorted(missing)}"
            )


def _validate_env_from_fact_resolves_to_map(
    nodes: list[ScenarioNodeDef], facts_by_name: dict[str, FactDef]
) -> None:
    for n in nodes:
        if n.env_from_fact is None:
            continue
        if n.env_from_fact not in facts_by_name:
            raise ValueError(
                f"node {n.id!r} env_from_fact {n.env_from_fact!r} must reference a declared fact"
            )
        fact = facts_by_name[n.env_from_fact]
        if not isinstance(fact.value, dict):
            raise ValueError(
                f"node {n.id!r} env_from_fact {n.env_from_fact!r} must reference a fact with map value"
            )


def resolve_argv(
    argv: list[str | ArgvFactRef | ArgvArtifactRef],
    facts_by_name: dict[str, FactDef],
    artifacts_by_id: dict[str, TopologyArtifactDef] | None = None,
    allowed_artifact_ids: set[str] | None = None,
    *,
    command_id: str | None = None,
    node_id: str | None = None,
) -> list[str]:
    """Resolve argv: substitute each ArgvFactRef with the fact's value (must be scalar)."""
    result: list[str] = []
    artifacts = artifacts_by_id or {}
    for item in argv:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, ArgvFactRef):
            if item.fact not in facts_by_name:
                raise ValueError(f"unknown fact in argv: {item.fact!r}")
            val = facts_by_name[item.fact].value
            if isinstance(val, dict):
                raise ValueError(
                    f"fact {item.fact!r} used in argv must have scalar value"
                )
            result.append(str(val))
        else:
            if item.artifact not in artifacts:
                raise ValueError(f"unknown artifact in argv: {item.artifact!r}")
            if (
                allowed_artifact_ids is not None
                and item.artifact not in allowed_artifact_ids
            ):
                cmd_part = f"command {command_id!r} " if command_id else ""
                node_part = f"node {node_id!r} " if node_id else ""
                raise ValueError(
                    f"{cmd_part}{node_part}references artifact {item.artifact!r} "
                    "but it is not granted in scenario nodes[].artifacts"
                )
            artifact = artifacts[item.artifact]
            result.append(f"/opt/artifacts/{artifact.filename}")
    return result


class ScenarioConfig(BaseModel):
    """Scenario configuration from scenario.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    topology: str = "testbed.yaml"
    facts: list[FactDef] = []
    nodes: list[ScenarioNodeDef]
    commands: list[CommandDef] = []

    @model_validator(mode="after")
    def validate_scenario(self) -> "ScenarioConfig":
        _validate_scenario_fact_names_unique(self.facts)
        _validate_scenario_node_ids_unique(self.nodes)
        _validate_scenario_command_ids_unique(self.commands)
        node_ids = {n.id for n in self.nodes}
        _validate_commands_reference_nodes(self.commands, node_ids)
        fact_names = {f.name for f in self.facts}
        _validate_fact_refs_resolve(self.commands, fact_names)
        facts_by_name = {f.name: f for f in self.facts}
        _validate_env_from_fact_resolves_to_map(self.nodes, facts_by_name)
        return self
