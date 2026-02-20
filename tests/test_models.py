"""Tests for topology and scenario models."""

import pytest

from testbed.models import (
    NetworkDef,
    NodeDef,
    ScenarioConfig,
    SpanConfig,
    TopologyConfig,
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


def test_scenario_config_defaults() -> None:
    """ScenarioConfig has sensible defaults."""
    s = ScenarioConfig(name="foo")
    assert s.topology == "testbed.yaml"
    assert s.nodes is None
    assert s.run_once is None


def test_scenario_config_run_once() -> None:
    """ScenarioConfig accepts run_once."""
    s = ScenarioConfig(name="bar", run_once=["tapirx"])
    assert s.run_once == ["tapirx"]


def test_scenario_config_span() -> None:
    """ScenarioConfig accepts span as list of sender/listener pairs."""
    s = ScenarioConfig(
        name="bar",
        span=[
            {"sender": "replay", "listener": "tapirx-live"},
        ],
    )
    assert s.span is not None
    assert len(s.span) == 1
    assert s.span[0].sender == "replay"
    assert s.span[0].listener == "tapirx-live"


def test_span_config_rejects_sender_equals_listener() -> None:
    """SpanConfig rejects sender == listener."""
    with pytest.raises(ValueError, match="sender and listener must differ"):
        SpanConfig(sender="same", listener="same")


def test_scenario_config_rejects_duplicate_span_senders() -> None:
    """ScenarioConfig rejects duplicate senders in span."""
    with pytest.raises(ValueError, match="unique sender"):
        ScenarioConfig(
            name="bar",
            span=[
                SpanConfig(sender="replay", listener="tapirx-live"),
                SpanConfig(sender="replay", listener="other"),
            ],
        )


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
