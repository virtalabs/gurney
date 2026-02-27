"""JSON Schema validation helpers for topology, scenario, and config YAML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Tuple

import yaml
from jsonschema import Draft7Validator


SCHEMAS_DIR = Path("schemas")


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_schema(schema_name: str) -> dict[str, Any]:
    project_root = Path.cwd()
    path = project_root / SCHEMAS_DIR / schema_name
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _format_errors(errors: Iterable[Any]) -> str:
    lines: list[str] = []
    for err in errors:
        # Build a JSON Pointer–like path for readability.
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        lines.append(f"- {path}: {err.message}")
    return "\n".join(lines)


def _validate_against_schema(
    data: Any,
    *,
    schema_name: str,
    source: Path,
) -> None:
    schema = _load_schema(schema_name)
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if not errors:
        return
    formatted = _format_errors(errors)
    raise ValueError(f"Schema validation failed for {source}:\n{formatted}")


def validate_topology_file(path: Path) -> None:
    """Validate a topology.yaml file against topology.schema.json."""
    data = _load_yaml(path)
    _validate_against_schema(data, schema_name="topology.schema.json", source=path)


def validate_scenario_file(path: Path) -> None:
    """Validate a scenario.yaml file against scenario.schema.json."""
    data = _load_yaml(path)
    _validate_against_schema(data, schema_name="scenario.schema.json", source=path)


def validate_config_file(path: Path) -> None:
    """Validate a config.yaml file against config.schema.json."""
    data = _load_yaml(path)
    _validate_against_schema(data, schema_name="config.schema.json", source=path)


def validate_all(project_root: Path) -> list[Tuple[Path, str | None]]:
    """Validate all known YAML configs under topologies/.

    Returns a list of (path, error_message_or_none) tuples.
    """
    project_root = project_root.resolve()
    results: list[Tuple[Path, str | None]] = []

    topology_files = sorted(
        project_root.glob("topologies/**/topology.yaml"),
        key=lambda p: p.as_posix(),
    )
    scenario_files = sorted(
        project_root.glob("topologies/**/scenarios/**/scenario.yaml"),
        key=lambda p: p.as_posix(),
    )
    config_files = sorted(
        project_root.glob("topologies/**/config.yaml"),
        key=lambda p: p.as_posix(),
    )

    for path in topology_files:
        try:
            validate_topology_file(path)
            results.append((path, None))
        except Exception as exc:  # noqa: BLE001
            results.append((path, str(exc)))

    for path in scenario_files:
        try:
            validate_scenario_file(path)
            results.append((path, None))
        except Exception as exc:  # noqa: BLE001
            results.append((path, str(exc)))

    for path in config_files:
        try:
            validate_config_file(path)
            results.append((path, None))
        except Exception as exc:  # noqa: BLE001
            results.append((path, str(exc)))

    return results

