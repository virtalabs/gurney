"""Tests for topology and scenario models."""

import pytest

from testbed.models import (
    ArgvFactRef,
    CommandDef,
    CommandRunDef,
    FactDef,
    NetworkDef,
    NodeDef,
    RetryConfig,
    ScenarioConfig,
    ScenarioNodeDef,
    TopologyConfig,
    resolve_argv,
)


def test_topology_parses_valid_yaml() -> None:
    """Valid topology parses without error."""
    top = TopologyConfig(
        networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
        nodes=[
            NodeDef(
                name="node1",
                kind="docker",
                image="redis:7",
                network="net1",
                ip="192.168.10.2",
            )
        ],
    )
    assert len(top.nodes) == 1
    assert top.nodes[0].name == "node1"


def test_topology_rejects_duplicate_ips() -> None:
    """Duplicate IPs raise validation error."""
    with pytest.raises(ValueError, match="Duplicate IP"):
        TopologyConfig(
            networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
            nodes=[
                NodeDef(
                    name="a",
                    kind="docker",
                    image="x",
                    network="net1",
                    ip="192.168.10.2",
                ),
                NodeDef(
                    name="b",
                    kind="docker",
                    image="x",
                    network="net1",
                    ip="192.168.10.2",
                ),
            ],
        )


def test_topology_rejects_ip_outside_cidr() -> None:
    """IP outside network CIDR raises validation error."""
    with pytest.raises(ValueError, match="not in network"):
        TopologyConfig(
            networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
            nodes=[
                NodeDef(
                    name="node1",
                    kind="docker",
                    image="x",
                    network="net1",
                    ip="10.0.0.1",
                )
            ],
        )


def test_topology_rejects_unknown_network() -> None:
    """Node referencing unknown network raises validation error."""
    with pytest.raises(ValueError, match="unknown network"):
        TopologyConfig(
            networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
            nodes=[
                NodeDef(
                    name="node1",
                    kind="docker",
                    image="x",
                    network="net99",
                    ip="192.168.10.2",
                )
            ],
        )


def test_topology_rejects_circular_depends_on() -> None:
    """Circular depends_on raises validation error."""
    with pytest.raises(ValueError, match="Circular dependency"):
        TopologyConfig(
            networks=[NetworkDef(name="net1", cidr="192.168.10.0/24")],
            nodes=[
                NodeDef(
                    name="a",
                    kind="docker",
                    image="x",
                    network="net1",
                    ip="192.168.10.2",
                    depends_on=["b"],
                ),
                NodeDef(
                    name="b",
                    kind="docker",
                    image="x",
                    network="net1",
                    ip="192.168.10.3",
                    depends_on=["a"],
                ),
            ],
        )


# --- Scenario DSL v2 ---


def test_scenario_config_minimal_v2() -> None:
    """ScenarioConfig v2 accepts minimal valid scenario."""
    s = ScenarioConfig(
        name="foo",
        topology="testbed.yaml",
        nodes=[
            ScenarioNodeDef(id="srv", build="up", networks=["net1"]),
        ],
        facts=[],
        commands=[],
    )
    assert s.name == "foo"
    assert s.topology == "testbed.yaml"
    assert len(s.nodes) == 1
    assert s.nodes[0].id == "srv"
    assert s.facts == []
    assert s.commands == []


def test_scenario_config_rejects_duplicate_node_ids() -> None:
    """ScenarioConfig rejects duplicate nodes[].id."""
    with pytest.raises(ValueError, match=r"nodes\[\].id must be unique"):
        ScenarioConfig(
            name="bar",
            nodes=[
                ScenarioNodeDef(id="a", build="up", networks=["net1"]),
                ScenarioNodeDef(id="a", build="up", networks=["net1"]),
            ],
        )


def test_scenario_config_rejects_duplicate_command_ids() -> None:
    """ScenarioConfig rejects duplicate commands[].id."""
    with pytest.raises(ValueError, match=r"commands\[\].id must be unique"):
        ScenarioConfig(
            name="bar",
            nodes=[
                ScenarioNodeDef(id="n", build="up", networks=["net1"]),
            ],
            commands=[
                CommandDef(
                    id="cmd1",
                    node="n",
                    run=CommandRunDef(argv=["true"]),
                    retry=RetryConfig(),
                ),
                CommandDef(
                    id="cmd1",
                    node="n",
                    run=CommandRunDef(argv=["false"]),
                    retry=RetryConfig(),
                ),
            ],
        )


def test_scenario_config_rejects_duplicate_fact_names() -> None:
    """ScenarioConfig rejects duplicate facts[].name."""
    with pytest.raises(ValueError, match=r"facts\[\].name must be unique"):
        ScenarioConfig(
            name="bar",
            nodes=[ScenarioNodeDef(id="n", build="up", networks=["net1"])],
            facts=[
                FactDef(name="x", value="1"),
                FactDef(name="x", value="2"),
            ],
        )


def test_scenario_config_rejects_command_unknown_node() -> None:
    """ScenarioConfig rejects command referencing undeclared node."""
    with pytest.raises(ValueError, match="reference a declared node"):
        ScenarioConfig(
            name="bar",
            nodes=[ScenarioNodeDef(id="n", build="up", networks=["net1"])],
            commands=[
                CommandDef(
                    id="c1",
                    node="other",
                    run=CommandRunDef(argv=["true"]),
                    retry=RetryConfig(),
                ),
            ],
        )


def test_scenario_config_rejects_retry_attempts_zero() -> None:
    """RetryConfig rejects attempts < 1."""
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        RetryConfig(attempts=0)


def test_scenario_config_rejects_retry_delay_negative() -> None:
    """RetryConfig rejects delay_s < 0."""
    with pytest.raises(ValueError, match="delay_s must be >= 0"):
        RetryConfig(delay_s=-1.0)


def test_scenario_node_rejects_span_and_networks() -> None:
    """ScenarioNodeDef rejects both span and networks."""
    with pytest.raises(ValueError, match="must not define both span and networks"):
        ScenarioNodeDef(
            id="n",
            build="run",
            networks=["net1"],
            span="listener",
        )


def test_scenario_config_rejects_unknown_fact_ref_in_argv() -> None:
    """ScenarioConfig rejects command argv referencing unknown fact."""
    with pytest.raises(ValueError, match="unknown fact"):
        ScenarioConfig(
            name="bar",
            nodes=[ScenarioNodeDef(id="n", build="up", networks=["net1"])],
            facts=[],
            commands=[
                CommandDef(
                    id="c1",
                    node="n",
                    run=CommandRunDef(
                        argv=["echo", ArgvFactRef(fact="missing_fact")]
                    ),
                    retry=RetryConfig(),
                ),
            ],
        )


def test_scenario_config_rejects_env_from_fact_not_map() -> None:
    """ScenarioConfig rejects env_from_fact when fact value is not a map."""
    with pytest.raises(ValueError, match="must reference a fact with map value"):
        ScenarioConfig(
            name="bar",
            nodes=[
                ScenarioNodeDef(
                    id="n",
                    build="up",
                    networks=["net1"],
                    env_from_fact="scalar_fact",
                ),
            ],
            facts=[FactDef(name="scalar_fact", value="not-a-map")],
        )


def test_resolve_argv_literals() -> None:
    """resolve_argv returns literals unchanged."""
    facts = {f.name: f for f in [FactDef(name="u", value="http://x")]}
    out = resolve_argv(["a", "b"], facts)
    assert out == ["a", "b"]


def test_resolve_argv_substitutes_fact() -> None:
    """resolve_argv substitutes ArgvFactRef with fact value."""
    facts = {f.name: f for f in [FactDef(name="url", value="http://api:8000")]}
    out = resolve_argv(["curl", ArgvFactRef(fact="url")], facts)
    assert out == ["curl", "http://api:8000"]


def test_resolve_argv_raises_on_unknown_fact() -> None:
    """resolve_argv raises on missing fact key."""
    with pytest.raises(ValueError, match="unknown fact"):
        resolve_argv([ArgvFactRef(fact="x")], {})


def test_node_def_cap_add_default() -> None:
    """NodeDef defaults cap_add to empty list."""
    n = NodeDef(
        name="srv",
        kind="docker",
        image="img",
        network="net1",
        ip="192.168.10.2",
    )
    assert n.cap_add == []


def test_node_def_cap_add_parses() -> None:
    """NodeDef with cap_add parses and preserves the list."""
    n = NodeDef(
        name="srv",
        kind="docker",
        image="img",
        network="net1",
        ip="192.168.10.2",
        cap_add=["NET_ADMIN"],
    )
    assert n.cap_add == ["NET_ADMIN"]
