"""Tests for live UI render helpers."""

from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from testbed.live_ui import LiveState, _body_renderable


def test_body_renderable_no_color_returns_text_with_sections() -> None:
    """No-color mode returns Text with up/cmd lines."""
    state = LiveState(
        scenario_name="demo",
        use_color=False,
        up_nodes=["postgres"],
        command_entries=[("verify", ["http_check", "http://api/"], "HTTP 200", "")],
    )
    rendered = _body_renderable(state)
    assert isinstance(rendered, Text)
    plain = rendered.plain
    assert "up postgres" in plain
    assert "cmd verify" in plain
    assert "$ http_check http://api/" in plain
    assert "HTTP 200" in plain


def test_body_renderable_color_returns_group() -> None:
    """Color mode returns Group to support rich panel rendering."""
    state = LiveState(
        scenario_name="demo",
        use_color=True,
        up_nodes=["postgres"],
        command_entries=[("verify", ["curl", "http://api/"], '{"ok":true}', "")],
    )
    rendered = _body_renderable(state)
    assert isinstance(rendered, Group)
    panels = [
        item.renderable
        for item in rendered.renderables
        if isinstance(item, Padding) and isinstance(item.renderable, Panel)
    ]
    assert panels, "Expected panelized command/output renderables in color mode"
