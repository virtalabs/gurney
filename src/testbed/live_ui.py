"""Live UI: dynamic banner, progress/spinner, and streamed event output for testbed run."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.padding import Padding
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from testbed.format_output import format_argv_for_display, format_command_output_one_line
from testbed.runner import RunEvent, RunResult, run_scenario

# ASCII banner line (no external font; simple box)
BANNER_TITLE = "VirtaLabs Testbed"


@dataclass
class LiveState:
    """Mutable state for the live renderable."""

    scenario_name: str = ""
    phase: str = "idle"  # idle | compose_up | commands | done
    up_nodes: list[str] = field(default_factory=list)
    command_entries: list[tuple[str, list[str], str, str]] = field(default_factory=list)
    current_command_id: str | None = None
    result: RunResult | None = None
    use_color: bool = True


def _banner_renderable(state: LiveState) -> Panel:
    """Build the top banner panel."""
    title = (
        f"{BANNER_TITLE}  •  {state.scenario_name}"
        if state.scenario_name
        else BANNER_TITLE
    )
    return Panel(
        Text(title, style="red"), border_style="blue", padding=(0, 2), expand=False
    )


def _progress_renderable(state: LiveState) -> Progress | None:
    """Spinner + phase text while running."""
    if state.phase == "idle" or state.phase == "done":
        return None
    phase_label = (
        "Bringing up services" if state.phase == "compose_up" else "Running commands"
    )
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        console=Console(),
        expand=False,
    )
    progress.add_task(phase_label, total=None)  # indeterminate
    return progress


def _is_json_output(stdout: str, stderr: str) -> tuple[bool, str]:
    """Return (True, pretty_json) if output is valid JSON, else (False, summary)."""
    raw = (stdout or "").strip()
    if not raw:
        raw = (stderr or "").strip()
    if not raw:
        return (False, "(no output)")
    try:
        data = json.loads(raw)
        return (True, json.dumps(data, indent=2))
    except (json.JSONDecodeError, ValueError):
        line = raw.split("\n")[0]
        summary = (line[:120] + "...") if len(line) > 120 else line
        return (False, summary or "(no output)")


def _command_formatted_output(stdout: str, stderr: str) -> tuple[bool, str]:
    """Return normalized command output payload for body rendering."""
    return _is_json_output(stdout, stderr)


def _command_panel(argv_line: str) -> Padding:
    """Render command line as a panel in live color mode."""
    syntax = Syntax(f"$ {argv_line}", "bash", theme="monokai", indent_guides=False)
    panel = Panel(syntax, border_style="cyan", padding=(0, 1), expand=False)
    return Padding(panel, (0, 0, 0, 5))


def _render_body_color(state: LiveState) -> Group:
    """Render body for color mode with rich panels for JSON output."""
    parts: list[Text | Panel | Padding] = []
    for node_id in state.up_nodes:
        parts.append(Text.from_markup(f"  [green]✓[/green] up {node_id}"))
    for cmd_id, argv, stdout, stderr in state.command_entries:
        argv_line = format_argv_for_display(argv)
        parts.append(Text.from_markup(f"  [cyan]▶[/cyan] cmd {cmd_id}"))
        parts.append(_command_panel(argv_line))
        is_json, formatted = _command_formatted_output(stdout, stderr)
        if is_json:
            syntax = Syntax(formatted, "json", theme="monokai", indent_guides=True)
            panel = Panel(syntax, border_style="green", padding=(0, 1), expand=False)
            parts.append(Padding(panel, (0, 0, 0, 5)))
        else:
            parts.append(Text(f"     {formatted}"))
        parts.append(Text(""))
    return Group(*parts)


def _render_body_plain(state: LiveState) -> Text:
    """Render body for no-color mode as plain text lines."""
    lines: list[str] = []
    for node_id in state.up_nodes:
        lines.append(f"  up {node_id}")
    for cmd_id, argv, stdout, stderr in state.command_entries:
        argv_line = format_argv_for_display(argv)
        _, formatted = _command_formatted_output(stdout, stderr)
        lines.append(f"  cmd {cmd_id}")
        lines.append(f"     $ {argv_line}")
        lines.append(f"     {formatted}")
        lines.append("")
    return Text("\n".join(lines))


def _body_renderable(state: LiveState) -> Group | Text:
    """Up nodes and command lines as Rich renderables; JSON in green panel under $ argv."""
    if state.use_color:
        return _render_body_color(state)
    return _render_body_plain(state)


def _one_line(stdout: str, stderr: str, max_len: int = 120) -> str:
    """JSON-aware one-line summary or first line truncated."""
    return format_command_output_one_line(
        stdout or "", stderr or "", verbose=False, max_len=max_len
    )


def _result_renderable(state: LiveState) -> Text | None:
    """Final PASS/FAIL line."""
    if state.result is None:
        return None
    r = state.result
    if r.passed:
        line = f"PASS {r.scenario} ({r.duration_s:.1f}s)"
        return Text(line, style="bold green") if state.use_color else Text(line)
    line = f"FAIL {r.scenario} ({r.duration_s:.1f}s)"
    return Text(line, style="bold red") if state.use_color else Text(line)


def _build_renderable(state: LiveState) -> Group:
    """Full layout: banner + progress + body + result."""
    parts = [_banner_renderable(state)]
    prog = _progress_renderable(state)
    if prog is not None:
        parts.append(prog)
    parts.append(_body_renderable(state))
    result_line = _result_renderable(state)
    if result_line is not None:
        parts.append(result_line)
        if state.result and not state.result.passed and state.result.error:
            parts.append(Text(state.result.error, style="red"))
    return Group(*parts)


def _make_handler(state: LiveState, live: Live):
    """Return an event handler that updates state and refreshes the live display."""

    def on_event(event: RunEvent) -> None:
        kind = event.kind
        if kind == "compose_up_started":
            state.phase = "compose_up"
        elif kind == "service_up":
            state.up_nodes.append(event.get("node_id", ""))
        elif kind == "command_started":
            state.phase = "commands"
            state.current_command_id = event.get("command_id")
        elif kind == "command_ended":
            state.command_entries.append(
                (
                    event.get("command_id", ""),
                    event.get("argv", []),
                    event.get("stdout", ""),
                    event.get("stderr", ""),
                )
            )
            state.current_command_id = None
        elif kind == "check_started":
            state.phase = "commands"
            state.current_command_id = event.get("command_id")
        elif kind == "check_passed":
            code = event.get("status_code", 200)
            state.command_entries.append(
                (
                    event.get("command_id", ""),
                    ["http_check", event.get("url", "")],
                    f"HTTP {code}",
                    "",
                )
            )
            state.current_command_id = None
        elif kind == "check_failed":
            state.command_entries.append(
                (
                    event.get("command_id", ""),
                    ["http_check", event.get("url", "")],
                    "",
                    event.get("error", ""),
                )
            )
            state.current_command_id = None
        live.update(_build_renderable(state))

    return on_event


def run_with_live_ui(
    scenario_dir: Path,
    *,
    keep: bool = False,
    verbose: bool = False,
    project_root: Path | None = None,
    use_color: bool = True,
) -> RunResult:
    """Run a scenario with live banner, progress, and streamed event output. Returns RunResult."""
    from testbed.runner import load_scenario

    scenario = load_scenario(scenario_dir)
    state = LiveState(scenario_name=scenario.name, use_color=use_color)
    console = Console(force_terminal=use_color, no_color=not use_color)

    with Live(console=console, refresh_per_second=4, transient=False) as live:
        live.update(_build_renderable(state))
        handler = _make_handler(state, live)
        result = run_scenario(
            scenario_dir,
            keep=keep,
            verbose=verbose,
            project_root=project_root,
            event_handler=handler,
        )
        state.result = result
        state.phase = "done"
        live.update(_build_renderable(state))
    return result
