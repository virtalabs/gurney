"""Typer CLI: run, list, teardown."""

from pathlib import Path

import typer

from testbed.compose import force_cleanup
from testbed.runner import run_scenario

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
        False, "--verbose", "-v", help="Show docker compose output"
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
