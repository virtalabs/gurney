"""Typer CLI: run, list, teardown."""

import logging
from pathlib import Path

import typer

from testbed.compose import force_cleanup
from testbed.runner import run_scenario, RunResult
from testbed.utility import ensure_log_dir, logger

VAR_LOG = Path("var") / "log"
DEFAULT_OUTPUT_MAX_LINE = 120


def _configure_logging() -> None:
    """Configure testbed logging to ./var/log/testbed.log; create ./var/log if needed."""
    log_dir = ensure_log_dir(Path.cwd() / VAR_LOG)
    log_file = log_dir / "testbed.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
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
    return (line[: max_len] + "...") if len(line) > max_len else line


def _echo_compose_section(result: RunResult) -> None:
    """Print Docker Compose up stdout/stderr."""
    typer.echo("------ Docker Compose (up) ------")
    if result.compose_up_stdout:
        typer.echo(result.compose_up_stdout.rstrip())
    if result.compose_up_stderr:
        typer.echo(result.compose_up_stderr.rstrip())
    typer.echo("")


def _echo_container_and_command_sections(result: RunResult) -> None:
    """Print container logs and command outputs (verbose: 4-tuple id, _argv, stdout, _stderr)."""
    if result.compose_up_service_logs:
        for service, logs in result.compose_up_service_logs:
            typer.echo(f"------ Container {service} ------")
            typer.echo(logs.rstrip() if logs else "(no output captured)")
            typer.echo("")
    if result.command_outputs:
        for command_id, _argv, stdout, _stderr in result.command_outputs:
            typer.echo(f"------ Command {command_id} ------")
            if stdout:
                typer.echo(stdout.rstrip())
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


def _echo_default_output(result: RunResult) -> None:
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
        typer.echo(f"     {argv_line}")
        typer.echo(f"     {_one_line_summary(stdout, stderr)}")
        typer.echo("")
    if result.passed:
        typer.echo(f"PASS {result.scenario} ({result.duration_s:.1f}s)")
    else:
        typer.echo(f"FAIL {result.scenario} ({result.duration_s:.1f}s)", err=True)
        if result.error:
            typer.echo(result.error, err=True)


def _echo_verbose_layers(result: RunResult) -> None:
    """Print structured verbose output: Docker Compose up, container(s), scenario output, Testbed."""
    _echo_compose_section(result)
    _echo_container_and_command_sections(result)
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
) -> None:
    """Run a scenario: topology up, health checks, teardown."""
    project_root = Path.cwd()
    scenarios_dir = project_root / "scenarios"
    scenario_dir = scenarios_dir / scenario

    if not scenario_dir.is_dir():
        typer.echo(f"Scenario not found: {scenario_dir}", err=True)
        logger.error("Scenario not found: %s", scenario_dir)
        raise typer.Exit(1)

    logger.info("Running scenario %s", scenario)
    result = run_scenario(
        scenario_dir,
        keep=keep,
        verbose=verbose,
        project_root=project_root,
    )

    if verbose and result.passed and (
        result.compose_up_stdout is not None
        or result.compose_up_service_logs
        or result.command_outputs
        or result.output_files
    ):
        _echo_verbose_layers(result)
    else:
        _echo_default_output(result)
    if result.passed:
        logger.info("PASS %s (%.1fs)", result.scenario, result.duration_s)
    else:
        logger.error("FAIL %s (%.1fs): %s", result.scenario, result.duration_s, result.error or "")
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
