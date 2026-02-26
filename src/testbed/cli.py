"""Typer CLI: run, list, teardown."""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from testbed.compose import force_cleanup
from testbed.console import should_use_color, use_live_ui
from testbed.format_output import (
    format_argv_for_display,
    format_command_output,
    format_command_output_one_line,
    parse_command_output_as_json,
)
from testbed.live_ui import run_with_live_ui
from testbed.reproduce import pull_and_verify
from testbed.runner import run_scenario, RunResult
from testbed.topology_index import get_or_build_index
from testbed.utility import ensure_log_dir, logger

VAR_LOG = Path("var") / "log"
DEFAULT_OUTPUT_MAX_LINE = 120

# ANSI codes (only used when use_color is True)
ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
CTX_JSON_KEY = "json_output"


def _configure_logging() -> None:
    """Configure testbed logging to ./var/log/testbed.log; create ./var/log if needed."""
    log_dir = ensure_log_dir(Path.cwd() / VAR_LOG)
    log_file = log_dir / "testbed.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    testbed_logger = logging.getLogger("testbed")
    testbed_logger.setLevel(logging.DEBUG)
    if not testbed_logger.handlers:
        testbed_logger.addHandler(handler)


def _one_line_summary(
    stdout: str, stderr: str, max_len: int = DEFAULT_OUTPUT_MAX_LINE
) -> str:
    """First line of stdout, or first line of stderr if stdout empty, or '(no output)'; truncate to max_len."""
    line = (stdout or "").strip().split("\n")[0] if (stdout or "").strip() else ""
    if not line:
        line = (stderr or "").strip().split("\n")[0] if (stderr or "").strip() else ""
    if not line:
        return "(no output)"
    return (line[:max_len] + "...") if len(line) > max_len else line


def _print_command_output(
    stdout: str,
    stderr: str,
    use_color: bool,
    indent: int = 5,
    verbose: bool = False,
) -> None:
    """Print command output: green syntax-highlighted JSON panel when use_color and valid JSON, else plain text."""
    is_json, formatted = parse_command_output_as_json(stdout, stderr)
    if use_color and is_json:
        syntax = Syntax(formatted, "json", theme="monokai", indent_guides=True)
        panel = Panel(syntax, border_style="green", padding=(0, 1), expand=False)
        Console().print(Padding(panel, (0, 0, 0, indent)))
    elif verbose and not is_json:
        typer.echo(format_command_output(stdout, stderr, verbose=True))
    else:
        typer.echo(f"     {formatted}" if indent else formatted)


def _echo_compose_section(result: RunResult) -> None:
    """Print Docker Compose up stdout/stderr."""
    typer.echo("------ Docker Compose (up) ------")
    if result.compose_up_stdout:
        typer.echo(result.compose_up_stdout.rstrip())
    if result.compose_up_stderr:
        typer.echo(result.compose_up_stderr.rstrip())
    typer.echo("")


def _echo_container_and_command_sections(
    result: RunResult, use_color: bool = False
) -> None:
    """Print container logs and command outputs (verbose: 4-tuple id, _argv, stdout, _stderr)."""
    if result.compose_up_service_logs:
        for service, logs in result.compose_up_service_logs:
            typer.echo(f"------ Container {service} ------")
            typer.echo(logs.rstrip() if logs else "(no output captured)")
            typer.echo("")
    if result.command_outputs:
        for command_id, _argv, stdout, stderr in result.command_outputs:
            typer.echo(f"------ Command {command_id} ------")
            if stdout or stderr:
                _print_command_output(
                    stdout or "", stderr or "", use_color, indent=5, verbose=True
                )
            typer.echo("")


def _echo_scenario_output_section(result: RunResult) -> None:
    """Print readable files from .build/output."""
    if not result.output_files:
        return
    typer.echo("------ Scenario output ------")
    for name, content in result.output_files:
        typer.echo(f"--- {name} ---")
        typer.echo(content.rstrip())
        typer.echo("")


def _echo_default_output(
    result: RunResult,
    use_color: bool = False,
) -> None:
    """Print default (non-verbose) output: up lines, cmd blocks, then PASS or FAIL."""
    _echo_up_nodes(result.up_node_ids or [])
    _echo_command_blocks(result.command_outputs or [], use_color=use_color)
    _echo_final_status(result, use_color=use_color)


def _echo_up_nodes(up_ids: list[str]) -> None:
    """Print node up lines for default output."""
    for node_id in up_ids:
        typer.echo(f"  up {node_id}")


def _echo_command_blocks(
    outputs: list[tuple[str, list[str], str, str]], use_color: bool = False
) -> None:
    """Print command blocks for default output."""
    if outputs:
        typer.echo("")
    for command_id, resolved_argv, stdout, stderr in outputs:
        typer.echo(f"  cmd {command_id}")
        argv_line = format_argv_for_display(resolved_argv)
        typer.echo(f"     $ {argv_line}")
        _print_command_output(stdout or "", stderr or "", use_color, indent=5, verbose=False)
        typer.echo("")


def _status_line(result: RunResult, use_color: bool) -> str:
    """Build PASS/FAIL status line with optional ANSI color."""
    line = (
        f"PASS {result.scenario} ({result.duration_s:.1f}s)"
        if result.passed
        else f"FAIL {result.scenario} ({result.duration_s:.1f}s)"
    )
    if not use_color:
        return line
    if result.passed:
        return f"{ANSI_GREEN}{line}{ANSI_RESET}"
    return f"{ANSI_RED}{line}{ANSI_RESET}"


def _echo_final_status(result: RunResult, use_color: bool = False) -> None:
    """Print final status line and optional error details."""
    if result.passed:
        typer.echo(_status_line(result, use_color))
        return
    typer.echo(_status_line(result, use_color), err=True)
    if result.error:
        typer.echo(result.error, err=True)


def _echo_verbose_layers(result: RunResult, use_color: bool = False) -> None:
    """Print structured verbose output: Docker Compose up, container(s), scenario output, Testbed."""
    _echo_compose_section(result)
    _echo_container_and_command_sections(result, use_color=use_color)
    _echo_scenario_output_section(result)
    typer.echo("------ Testbed ------")
    typer.echo(f"PASS {result.scenario} ({result.duration_s:.1f}s)")


def _resolve_scenario_dir(project_root: Path, scenario: str) -> Path:
    """Resolve scenario ref to scenario directory via topology index."""
    try:
        index, _ = get_or_build_index(project_root)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    for topology in index.topologies:
        for entry in topology.scenarios:
            if entry.ref == scenario:
                return project_root / Path(entry.path).parent
    raise RuntimeError(f"Scenario not found: {scenario}")


def _resolve_topology_id(project_root: Path, target: str) -> str:
    """Resolve topology id from topology id or scenario ref."""
    if "/" in target:
        scenario = target
        try:
            index, _ = get_or_build_index(project_root)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        for topology in index.topologies:
            for entry in topology.scenarios:
                if entry.ref == scenario:
                    return topology.id
        raise RuntimeError(f"Scenario not found: {scenario}")
    topology_id = target
    topology_path = project_root / "topologies" / topology_id / "topology.yaml"
    if not topology_path.exists():
        raise RuntimeError(f"Topology not found: {topology_id}")
    return topology_id


def _emit_run_output(
    result: RunResult, *, live_used: bool, verbose: bool, use_color: bool
) -> None:
    """Emit run output for live or classic mode."""
    if live_used:
        return  # live UI already printed output
    should_show_verbose = verbose and result.passed and (
        result.compose_up_stdout is not None
        or result.compose_up_service_logs
        or result.command_outputs
        or result.output_files
    )
    if should_show_verbose:
        _echo_verbose_layers(result, use_color=use_color)
        return
    _echo_default_output(result, use_color=use_color)


def _ts_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _resolve_json_mode(ctx: typer.Context, command_json_output: bool) -> bool:
    root_json_output = bool((ctx.obj or {}).get(CTX_JSON_KEY, False))
    if root_json_output and command_json_output:
        raise typer.BadParameter(
            "Do not pass --json twice; use either root-level or command-level form."
        )
    return root_json_output or command_json_output


def _emit_ndjson(command: str, event: str, payload: dict[str, Any] | None = None) -> None:
    envelope: dict[str, Any] = {"event": event, "command": command, "ts": _ts_utc()}
    if payload is not None:
        envelope["payload"] = payload
    typer.echo(json.dumps(envelope, separators=(",", ":")))


def _render_list_human(use_color: bool, topology_rows: list[tuple[str, list[str]]]) -> None:
    if not use_color:
        typer.echo("Available scenarios:")
        for topology_id, scenario_refs in topology_rows:
            typer.echo(f"\n{topology_id}")
            for scenario_ref in scenario_refs:
                typer.echo(f"  - {scenario_ref}")
        return

    header = Text("VirtaLabs Testbed  •  Available scenarios", style="bold red")
    Console().print(Panel(header, border_style="blue", padding=(0, 1), expand=False))
    for topology_id, scenario_refs in topology_rows:
        typer.echo(f"  topology {topology_id}")
        for scenario_ref in scenario_refs:
            typer.echo(f"    - {scenario_ref}")
        typer.echo("")


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def _main(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Emit newline-delimited JSON (NDJSON) events."
    ),
) -> None:
    """Configure logging before any command."""
    _configure_logging()
    ctx.obj = {CTX_JSON_KEY: json_output}


@app.command()
def run(
    ctx: typer.Context,
    scenario: str = typer.Argument(
        ..., help="Scenario ref as <topology-id>/<scenario-id>"
    ),
    keep: bool = typer.Option(False, "--keep", "-k", help="Do not teardown after run"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show Docker and container output after a successful run",
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable colored output (also respects NO_COLOR env).",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit newline-delimited JSON (NDJSON) events."
    ),
) -> None:
    """Run a scenario: topology up, health checks, teardown."""
    project_root = Path.cwd()
    json_mode = _resolve_json_mode(ctx, json_output)
    try:
        scenario_dir = _resolve_scenario_dir(project_root, scenario)
    except RuntimeError as exc:
        if json_mode:
            _emit_ndjson("run", "failed", {"scenario": scenario, "error": str(exc)})
        else:
            typer.echo(str(exc), err=True)
        logger.error("Scenario not found: %s", scenario)
        raise typer.Exit(1) from exc

    if not scenario_dir.is_dir():
        message = f"Scenario directory not found: {scenario_dir}"
        if json_mode:
            _emit_ndjson("run", "failed", {"scenario": scenario, "error": message})
        else:
            typer.echo(message, err=True)
        raise typer.Exit(1)

    use_color = should_use_color(no_color)
    logger.info("Running scenario %s", scenario)
    live_used = use_live_ui() and not json_mode

    if json_mode:
        _emit_ndjson("run", "started", {"scenario": scenario, "keep": keep})
        run_failed = False

        def _run_event_handler(event: Any) -> None:
            _emit_ndjson("run", event.kind, dict(event.payload))

        result = run_scenario(
            scenario_dir,
            keep=keep,
            verbose=verbose,
            project_root=project_root,
            event_handler=_run_event_handler,
        )
        terminal_event = "completed" if result.passed else "failed"
        terminal_payload = {
            "scenario": result.scenario,
            "duration_s": result.duration_s,
            "passed": result.passed,
        }
        if result.error:
            terminal_payload["error"] = result.error
        _emit_ndjson("run", terminal_event, terminal_payload)
        run_failed = not result.passed
    elif live_used:
        result = run_with_live_ui(
            scenario_dir,
            keep=keep,
            verbose=verbose,
            project_root=project_root,
            use_color=use_color,
        )
        run_failed = not result.passed
    else:
        result = run_scenario(
            scenario_dir,
            keep=keep,
            verbose=verbose,
            project_root=project_root,
        )
        run_failed = not result.passed

    if not json_mode:
        _emit_run_output(
            result,
            live_used=live_used,
            verbose=verbose,
            use_color=use_color,
        )
    if result.passed:
        logger.info("PASS %s (%.1fs)", result.scenario, result.duration_s)
    else:
        logger.error(
            "FAIL %s (%.1fs): %s",
            result.scenario,
            result.duration_s,
            result.error or "",
        )
    if run_failed:
        raise typer.Exit(1)


@app.command(name="list")
def list_scenarios(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Emit newline-delimited JSON (NDJSON) events."
    ),
) -> None:
    """List available scenarios grouped by topology."""
    project_root = Path.cwd()
    json_mode = _resolve_json_mode(ctx, json_output)
    try:
        index, _ = get_or_build_index(project_root)
    except ValueError as exc:
        if json_mode:
            _emit_ndjson("list", "failed", {"error": str(exc)})
            raise typer.Exit(1) from exc
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    topology_rows = [
        (topology.id, [scenario.ref for scenario in topology.scenarios])
        for topology in index.topologies
    ]
    if json_mode:
        _emit_ndjson("list", "started")
        for topology_id, scenario_refs in topology_rows:
            _emit_ndjson(
                "list",
                "topology",
                {"topology_id": topology_id, "scenarios": scenario_refs},
            )
        _emit_ndjson(
            "list",
            "completed",
            {
                "topology_count": len(topology_rows),
                "scenario_count": sum(len(row[1]) for row in topology_rows),
            },
        )
        return

    _render_list_human(should_use_color(False), topology_rows)


@app.command()
def pull(
    ctx: typer.Context,
    target: str = typer.Argument(
        ..., help="Topology id or scenario ref (<topology-id>/<scenario-id>)"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit newline-delimited JSON (NDJSON) events."
    ),
) -> None:
    """Pull/build/verify reproducibility assets for a selected topology config."""
    project_root = Path.cwd()
    json_mode = _resolve_json_mode(ctx, json_output)
    try:
        topology_id = _resolve_topology_id(project_root, target)
    except RuntimeError as exc:
        if json_mode:
            _emit_ndjson("pull", "failed", {"error": str(exc)})
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    try:
        if json_mode:
            _emit_ndjson("pull", "started", {"target": target, "topology_id": topology_id})

            def _pull_reporter(event: str, payload: dict[str, Any]) -> None:
                _emit_ndjson("pull", event, payload)

            pull_and_verify(topology_id, reporter=_pull_reporter, emit_text=False)
            _emit_ndjson("pull", "completed", {"topology_id": topology_id})
            return

        pull_and_verify(topology_id)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        if json_mode:
            _emit_ndjson("pull", "failed", {"error": str(exc), "topology_id": topology_id})
            raise typer.Exit(1) from exc
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def teardown(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Emit newline-delimited JSON (NDJSON) events."
    ),
) -> None:
    """Force-remove all testbed-managed Docker resources."""
    json_mode = _resolve_json_mode(ctx, json_output)
    if json_mode:
        _emit_ndjson("teardown", "started")
    force_cleanup()
    if json_mode:
        _emit_ndjson("teardown", "completed")
        return
    typer.echo("Teardown complete.")


if __name__ == "__main__":
    app()
