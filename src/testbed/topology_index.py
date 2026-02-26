"""Lazy index for co-located topologies and scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

import yaml

INDEX_VERSION = 1
INDEX_DIR = Path("var") / "index"
INDEX_FILENAME = "topologies.index.yaml"
STATE_FILENAME = "topologies.index.state.json"

TOPOLOGY_GLOB = "topologies/**/topology.yaml"
SCENARIO_GLOB = "topologies/**/scenarios/**/scenario.yaml"


@dataclass(frozen=True)
class ScenarioIndexEntry:
    """Indexed scenario metadata."""

    id: str
    ref: str
    path: str


@dataclass(frozen=True)
class TopologyIndexEntry:
    """Indexed topology metadata with its scenarios."""

    id: str
    topology_path: str
    scenarios: list[ScenarioIndexEntry]


@dataclass(frozen=True)
class TopologyIndex:
    """On-disk index model."""

    version: int
    generated_at: str
    fingerprint: str
    topologies: list[TopologyIndexEntry]


def _index_path(project_root: Path) -> Path:
    return project_root / INDEX_DIR / INDEX_FILENAME


def _state_path(project_root: Path) -> Path:
    return project_root / INDEX_DIR / STATE_FILENAME


def _iter_relevant_files(project_root: Path) -> list[Path]:
    files: list[Path] = []
    files.extend(project_root.glob(TOPOLOGY_GLOB))
    files.extend(project_root.glob(SCENARIO_GLOB))
    return sorted({p.resolve() for p in files if p.is_file()}, key=lambda p: p.as_posix())


def _fingerprint_for_files(project_root: Path, files: list[Path]) -> str:
    rows: list[str] = []
    for path in files:
        rel = path.relative_to(project_root).as_posix()
        stat = path.stat()
        rows.append(f"{rel}|{stat.st_size}|{stat.st_mtime_ns}")
    payload = "\n".join(sorted(rows)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        temp_path = Path(tmp.name)
    temp_path.replace(path)


def _topology_id_from_path(project_root: Path, topology_path: Path) -> str:
    rel = topology_path.relative_to(project_root)
    parts = rel.parts
    if len(parts) < 3 or parts[0] != "topologies" or parts[-1] != "topology.yaml":
        raise ValueError(f"Invalid topology path layout: {rel}")
    return parts[1]


def _scenario_index_entries(project_root: Path, topology_id: str) -> list[ScenarioIndexEntry]:
    scenarios_root = project_root / "topologies" / topology_id / "scenarios"
    if not scenarios_root.is_dir():
        return []

    entries: list[ScenarioIndexEntry] = []
    for scenario_file in sorted(scenarios_root.glob("**/scenario.yaml")):
        if not scenario_file.is_file():
            continue
        scenario_id = scenario_file.parent.name
        ref = f"{topology_id}/{scenario_id}"
        entries.append(
            ScenarioIndexEntry(
                id=scenario_id,
                ref=ref,
                path=scenario_file.relative_to(project_root).as_posix(),
            )
        )
    return entries


def _build_index(project_root: Path, fingerprint: str) -> TopologyIndex:
    topology_files = sorted(project_root.glob(TOPOLOGY_GLOB), key=lambda p: p.as_posix())
    topologies: list[TopologyIndexEntry] = []
    refs_seen: dict[str, str] = {}

    for topology_file in topology_files:
        if not topology_file.is_file():
            continue
        topology_id = _topology_id_from_path(project_root, topology_file.resolve())
        scenarios = _scenario_index_entries(project_root, topology_id)
        for scenario in scenarios:
            if scenario.ref in refs_seen:
                first_path = refs_seen[scenario.ref]
                raise ValueError(
                    "Duplicate scenario_ref detected: "
                    f"{scenario.ref}\n- {first_path}\n- {scenario.path}"
                )
            refs_seen[scenario.ref] = scenario.path
        topologies.append(
            TopologyIndexEntry(
                id=topology_id,
                topology_path=topology_file.relative_to(project_root).as_posix(),
                scenarios=sorted(scenarios, key=lambda s: s.ref),
            )
        )

    return TopologyIndex(
        version=INDEX_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        fingerprint=fingerprint,
        topologies=sorted(topologies, key=lambda t: t.id),
    )


def _serialize_index(index: TopologyIndex) -> dict[str, Any]:
    return {
        "version": index.version,
        "generated_at": index.generated_at,
        "fingerprint": index.fingerprint,
        "topologies": [
            {
                "id": topology.id,
                "topology_path": topology.topology_path,
                "scenarios": [
                    {"id": s.id, "ref": s.ref, "path": s.path} for s in topology.scenarios
                ],
            }
            for topology in index.topologies
        ],
    }


def _parse_index(raw: dict[str, Any]) -> TopologyIndex:
    topologies: list[TopologyIndexEntry] = []
    for topology in raw.get("topologies", []):
        scenarios = [
            ScenarioIndexEntry(
                id=str(s["id"]),
                ref=str(s["ref"]),
                path=str(s["path"]),
            )
            for s in topology.get("scenarios", [])
        ]
        topologies.append(
            TopologyIndexEntry(
                id=str(topology["id"]),
                topology_path=str(topology["topology_path"]),
                scenarios=scenarios,
            )
        )
    return TopologyIndex(
        version=int(raw.get("version", INDEX_VERSION)),
        generated_at=str(raw.get("generated_at", "")),
        fingerprint=str(raw.get("fingerprint", "")),
        topologies=topologies,
    )


def _write_index_and_state(project_root: Path, index: TopologyIndex) -> None:
    state = {"version": INDEX_VERSION, "last_fingerprint": index.fingerprint}
    index_content = yaml.safe_dump(_serialize_index(index), sort_keys=False)
    state_content = json.dumps(state, indent=2) + "\n"
    _atomic_write_text(_index_path(project_root), index_content)
    _atomic_write_text(_state_path(project_root), state_content)


def _read_state(project_root: Path) -> dict[str, Any] | None:
    path = _state_path(project_root)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _read_index(project_root: Path) -> TopologyIndex | None:
    path = _index_path(project_root)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return _parse_index(raw)
    except (KeyError, TypeError, ValueError):
        return None


def get_or_build_index(project_root: Path) -> tuple[TopologyIndex, bool]:
    """Load index from disk when valid, otherwise regenerate lazily.

    Returns (index, regenerated).
    """
    project_root = project_root.resolve()
    relevant_files = _iter_relevant_files(project_root)
    fingerprint = _fingerprint_for_files(project_root, relevant_files)
    state = _read_state(project_root)
    index = _read_index(project_root)

    should_regenerate = index is None or state is None
    if not should_regenerate:
        assert state is not None
        should_regenerate = state.get("last_fingerprint") != fingerprint

    if should_regenerate:
        index = _build_index(project_root, fingerprint)
        _write_index_and_state(project_root, index)
        return index, True

    assert index is not None
    return index, False
