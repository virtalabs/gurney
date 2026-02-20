"""Typer CLI: run, list, teardown."""

from pathlib import Path

import typer

from testbed.compose import force_cleanup
from testbed.runner import run_scenario, RunResult


def _echo_verbose_layers(result: RunResult) -> None:
    """Print structured verbose output: Docker Compose up, container(s), scenario output, Testbed."""
    # Docker Compose (up)
    typer.echo("------ Docker Compose (up) ------")
    if result.compose_up_stdout:
        typer.echo(result.compose_up_stdout.rstrip())
    if result.compose_up_stderr:
        typer.echo(result.compose_up_stderr.rstrip())
    typer.echo("")

    # Container(s) — first compose-up service logs, then run_once outputs
    if result.compose_up_service_logs:
        for service, logs in result.compose_up_service_logs:
            typer.echo(f"------ Container {service} ------")
            if logs:
                typer.echo(logs.rstrip())
            else:
                typer.echo("(no output captured)")
            typer.echo("")
    if result.run_once_outputs:
        for service, stdout, _stderr in result.run_once_outputs:
            typer.echo(f"------ Container {service} ------")
            if stdout:
                typer.echo(stdout.rstrip())
            typer.echo("")

    # Scenario output (readable files from .build/output)
    if result.output_files:
        typer.echo("------ Scenario output ------")
        for name, content in result.output_files:
            typer.echo(f"--- {name} ---")
            typer.echo(content.rstrip())
            typer.echo("")

    # Testbed
    typer.echo("------ Testbed ------")
    typer.echo(f"PASS {result.scenario} ({result.duration_s:.1f}s)")


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
)


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
        raise typer.Exit(1)

    result = run_scenario(
        scenario_dir,
        keep=keep,
        verbose=verbose,
        project_root=project_root,
    )

    if result.passed:
        if verbose and (
            result.compose_up_stdout is not None
            or result.compose_up_service_logs
            or result.run_once_outputs
            or result.output_files
        ):
            _echo_verbose_layers(result)
        else:
            typer.echo(f"PASS {result.scenario} ({result.duration_s:.1f}s)")
    else:
        typer.echo(f"FAIL {result.scenario} ({result.duration_s:.1f}s)", err=True)
        if result.error:
            typer.echo(result.error, err=True)
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
