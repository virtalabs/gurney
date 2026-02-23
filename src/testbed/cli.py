"""Typer CLI: run, list, teardown."""

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax

from testbed.compose import force_cleanup
from testbed.console import UIMode, should_use_color, use_live_ui
from testbed.format_output import (
    format_command_output,
    format_command_output_one_line,
    parse_command_output_as_json,
)
from testbed.live_ui import run_with_live_ui
from testbed.runner import run_scenario, RunResult
from testbed.utility import ensure_log_dir, logger

VAR_LOG = Path("var") / "log"
DEFAULT_OUTPUT_MAX_LINE = 120

# ANSI codes (only used when use_color is True)
ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"


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
    up_ids = result.up_node_ids or []
    for node_id in up_ids:
        typer.echo(f"  up {node_id}")
    outputs = result.command_outputs or []
    if outputs:
        typer.echo("")
    for command_id, resolved_argv, stdout, stderr in outputs:
        typer.echo(f"  cmd {command_id}")
        argv_line = " ".join(resolved_argv) if resolved_argv else ""
        typer.echo(f"     $ {argv_line}")
        _print_command_output(stdout or "", stderr or "", use_color, indent=5, verbose=False)
        typer.echo("")
    if result.passed:
        line = f"PASS {result.scenario} ({result.duration_s:.1f}s)"
        typer.echo(f"{ANSI_GREEN}{line}{ANSI_RESET}" if use_color else line)
    else:
        line = f"FAIL {result.scenario} ({result.duration_s:.1f}s)"
        out = f"{ANSI_RED}{line}{ANSI_RESET}" if use_color else line
        typer.echo(out, err=True)
        if result.error:
            typer.echo(result.error, err=True)


def _echo_verbose_layers(result: RunResult, use_color: bool = False) -> None:
    """Print structured verbose output: Docker Compose up, container(s), scenario output, Testbed."""
    _echo_compose_section(result)
    _echo_container_and_command_sections(result, use_color=use_color)
    _echo_scenario_output_section(result)
    typer.echo("------ Testbed ------")
    typer.echo(f"PASS {result.scenario} ({result.duration_s:.1f}s)")


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def _main() -> None:
    """Configure logging before any command."""
    _configure_logging()


@app.command()
def run(
    scenario: str = typer.Argument(
        ..., help="Scenario name (directory under scenarios/)"
    ),
    keep: bool = typer.Option(False, "--keep", "-k", help="Do not teardown after run"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show Docker and container output after a successful run",
    ),
    ui: UIMode = typer.Option(
        "live",
        "--ui",
        help="Output style: live (banner, progress, streamed) or classic (plain).",
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Disable colored output (also respects NO_COLOR env).",
    ),
) -> None:
    """Run a scenario: topology up, health checks, teardown."""
    project_root = Path.cwd()
    scenarios_dir = project_root / "scenarios"
    scenario_dir = scenarios_dir / scenario

    if not scenario_dir.is_dir():
        typer.echo(f"Scenario not found: {scenario_dir}", err=True)
        logger.error("Scenario not found: %s", scenario_dir)
        raise typer.Exit(1)

    use_color = should_use_color(no_color)
    logger.info("Running scenario %s", scenario)
    if use_live_ui(ui):
        result = run_with_live_ui(
            scenario_dir,
            keep=keep,
            verbose=verbose,
            project_root=project_root,
            use_color=use_color,
        )
    else:
        result = run_scenario(
            scenario_dir,
            keep=keep,
            verbose=verbose,
            project_root=project_root,
        )

    if use_live_ui(ui):
        pass  # live UI already printed output
    elif (
        verbose
        and result.passed
        and (
            result.compose_up_stdout is not None
            or result.compose_up_service_logs
            or result.command_outputs
            or result.output_files
        )
    ):
        _echo_verbose_layers(result, use_color=use_color)
    else:
        _echo_default_output(result, use_color=use_color)
    if result.passed:
        logger.info("PASS %s (%.1fs)", result.scenario, result.duration_s)
    else:
        logger.error(
            "FAIL %s (%.1fs): %s",
            result.scenario,
            result.duration_s,
            result.error or "",
        )
    if not result.passed:
        raise typer.Exit(1)


@app.command(name="list")
def list_scenarios() -> None:
    """List available scenarios."""
    scenarios_dir = Path.cwd() / "scenarios"
    if not scenarios_dir.is_dir():
        return

    for path in sorted(scenarios_dir.iterdir()):
        if path.is_dir() and (path / "scenario.yaml").exists():
            typer.echo(path.name)


@app.command()
def teardown() -> None:
    """Force-remove all testbed-managed Docker resources."""
    force_cleanup()
    typer.echo("Teardown complete.")


if __name__ == "__main__":
    app()
