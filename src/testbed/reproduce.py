"""Image pinning and digest verification for reproducibility."""

import hashlib
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class ArtifactSpec:
    filename: str
    url: str


@dataclass(frozen=True)
class LocalImageSpec:
    dockerfile: str
    tag: str


@dataclass(frozen=True)
class ReproduceConfig:
    locked_images: tuple[str, ...]
    local_images: tuple[LocalImageSpec, ...]
    artifacts: tuple[ArtifactSpec, ...]
    login_required_images: tuple[str, ...]
    fixture_pcap_root: Path
    cache_pcap_root: Path


def _as_str_list(value: Any, field: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RuntimeError(f"Invalid {field} in {path}: expected list[str]")
    return tuple(value)


def _parse_local_images(value: Any, path: Path) -> tuple[LocalImageSpec, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"Invalid local_images in {path}: expected list of mappings")
    parsed: list[LocalImageSpec] = []
    for idx, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise RuntimeError(f"Invalid local_images[{idx}] in {path}: expected mapping")
        dockerfile = entry.get("dockerfile")
        tag = entry.get("tag")
        if not isinstance(dockerfile, str) or not isinstance(tag, str):
            raise RuntimeError(
                f"Invalid local_images[{idx}] in {path}: 'dockerfile' and 'tag' must be strings"
            )
        parsed.append(LocalImageSpec(dockerfile=dockerfile, tag=tag))
    return tuple(parsed)


def _parse_artifacts(value: Any, path: Path) -> tuple[ArtifactSpec, ...]:
    if not isinstance(value, list):
        raise RuntimeError(f"Invalid artifacts in {path}: expected list of mappings")
    parsed: list[ArtifactSpec] = []
    for idx, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise RuntimeError(f"Invalid artifacts[{idx}] in {path}: expected mapping")
        filename = entry.get("filename")
        url = entry.get("url")
        if not isinstance(filename, str) or not isinstance(url, str):
            raise RuntimeError(
                f"Invalid artifacts[{idx}] in {path}: 'filename' and 'url' must be strings"
            )
        parsed.append(ArtifactSpec(filename=filename, url=url))
    return tuple(parsed)


def _load_config(project_root: Path, topology_id: str) -> ReproduceConfig:
    """Load topology-scoped reproducibility config from topologies/<id>/config.yaml."""
    path = project_root / "topologies" / topology_id / CONFIG_FILENAME
    if not path.exists():
        raise RuntimeError(
            f"Topology config not found:\n  {path}\n"
            f"Expected config filename: {CONFIG_FILENAME}"
        )
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"Invalid config in {path}: expected top-level mapping")
    required = [
        "locked_images",
        "local_images",
        "artifacts",
        "login_required_images",
        "fixture_pcap_root",
        "cache_pcap_root",
    ]
    missing = [key for key in required if key not in raw]
    if missing:
        raise RuntimeError(f"Invalid config in {path}: missing required keys {missing}")
    fixture_pcap_root = raw["fixture_pcap_root"]
    cache_pcap_root = raw["cache_pcap_root"]
    if not isinstance(fixture_pcap_root, str) or not isinstance(cache_pcap_root, str):
        raise RuntimeError(
            f"Invalid fixture/cache roots in {path}: expected string paths"
        )
    return ReproduceConfig(
        locked_images=_as_str_list(raw["locked_images"], "locked_images", path),
        local_images=_parse_local_images(raw["local_images"], path),
        artifacts=_parse_artifacts(raw["artifacts"], path),
        login_required_images=_as_str_list(
            raw["login_required_images"], "login_required_images", path
        ),
        fixture_pcap_root=Path(fixture_pcap_root),
        cache_pcap_root=Path(cache_pcap_root),
    )


def _parse_digest_from_inspect(stdout: str) -> str:
    """Extract sha256:... from docker inspect output. Handles image@sha256:... format."""
    line = stdout.strip()
    if "@" in line:
        return line.split("@", 1)[1]
    return line


def _get_image_digest(image: str) -> str:
    """Pull image and return its digest (sha256:...)."""
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", image],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return _parse_digest_from_inspect(result.stdout)
    result2 = subprocess.run(
        ["docker", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
    )
    if result2.returncode == 0 and result2.stdout.strip():
        return result2.stdout.strip()
    raise RuntimeError(f"Could not get digest for {image}: {result.stderr}")


def _load_lockfile(lock_path: Path) -> dict:
    """Load images.lock. Returns full lock data dict."""
    if not lock_path.exists():
        return {}
    with open(lock_path) as f:
        data = yaml.safe_load(f) or {}
    return dict(data)


def _save_lockfile(lock_path: Path, lock_data: dict) -> None:
    """Save images.lock."""
    with open(lock_path, "w") as f:
        yaml.dump(lock_data, f, default_flow_style=False, sort_keys=True)


def _sha256_file(path: Path) -> str:
    """Compute SHA256 of file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_local_image(project_root: Path, dockerfile_name: str, image_tag: str) -> None:
    """Build a local image if Dockerfile exists."""
    dockerfile = project_root / "docker" / dockerfile_name
    if not dockerfile.exists():
        return
    print(f"  Building {image_tag}...")
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(dockerfile),
            "-t",
            image_tag,
            str(project_root),
        ],
        check=True,
        capture_output=True,
    )
    print(f"  {image_tag}: OK")


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _fixture_pcap_path(project_root: Path, config: ReproduceConfig, filename: str) -> Path:
    return project_root / config.fixture_pcap_root / filename


def _cache_pcap_path(project_root: Path, config: ReproduceConfig, filename: str) -> Path:
    return project_root / config.cache_pcap_root / filename


def _sync_or_fetch_artifacts(
    lock_data: dict, project_root: Path, config: ReproduceConfig
) -> None:
    """Ensure configured artifacts exist in cache, preferring committed topology fixtures."""
    cache_dir = project_root / config.cache_pcap_root
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_data.setdefault("pcaps", {})

    for artifact in config.artifacts:
        filename = artifact.filename
        fixture_path = _fixture_pcap_path(project_root, config, filename)
        cache_path = _cache_pcap_path(project_root, config, filename)
        expected = lock_data.get("pcaps", {}).get(filename, {}).get("sha256")

        if fixture_path.exists():
            actual = _sha256_file(fixture_path)
            if expected and actual != expected:
                raise RuntimeError(
                    f"SHA256 mismatch for fixture {filename}\n"
                    f"  expected: {expected}\n"
                    f"  actual:   {actual}\n"
                    f"  fixture:  {fixture_path}"
                )
            if not cache_path.exists() or _sha256_file(cache_path) != actual:
                _copy_file(fixture_path, cache_path)
                print(f"  Synced fixture {filename} -> {config.cache_pcap_root}")
            lock_data["pcaps"][filename] = {"url": artifact.url, "sha256": actual}
            print(f"  {filename}: OK (fixture)")
            continue

        if cache_path.exists():
            actual = _sha256_file(cache_path)
            if expected and actual != expected:
                raise RuntimeError(
                    f"SHA256 mismatch for cached artifact {filename}\n"
                    f"  expected: {expected}\n"
                    f"  actual:   {actual}\n"
                    f"  cache:    {cache_path}"
                )
            lock_data["pcaps"][filename] = {"url": artifact.url, "sha256": actual}
            print(f"  {filename}: OK (cache)")
            continue

        print(f"  Fetching {filename}...")
        urllib.request.urlretrieve(artifact.url, cache_path)
        actual = _sha256_file(cache_path)
        lock_data["pcaps"][filename] = {"url": artifact.url, "sha256": actual}
        print(f"  {filename}: {actual}")


def pull_and_verify(topology_id: str) -> None:
    """Pull/build/verify using topology-scoped config.yaml."""
    project_root = Path.cwd()
    config = _load_config(project_root, topology_id)
    lock_path = project_root / "images.lock"
    lock_data = _load_lockfile(lock_path)

    print(f"Topology: {topology_id}")
    print(f"Config: topologies/{topology_id}/{CONFIG_FILENAME}")

    for local in config.local_images:
        _build_local_image(project_root, local.dockerfile, local.tag)

    login_required_images = set(config.login_required_images)
    for image in config.locked_images:
        try:
            actual = _get_image_digest(image)
        except (RuntimeError, subprocess.CalledProcessError):
            if image in login_required_images:
                print(f"  {image}: skip (pull failed, may require docker login)")
                continue
            raise
        expected = lock_data.get(image)

        if expected is None:
            lock_data[image] = actual
            print(f"  {image}: {actual}")
        elif actual != expected:
            raise RuntimeError(
                f"Digest mismatch for {image}\n  expected: {expected}\n  actual:   {actual}"
            )
        else:
            print(f"  {image}: OK")

    _sync_or_fetch_artifacts(lock_data, project_root, config)

    _save_lockfile(lock_path, lock_data)
