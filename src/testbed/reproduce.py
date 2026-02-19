"""Image pinning and digest verification for reproducibility."""

import subprocess
from pathlib import Path

import yaml


# Third-party images from testbed.yaml (exclude ${VAR} images)
LOCKED_IMAGES = [
    "postgres:16.6-alpine",
    "redis:7.4-alpine",
    "orthancteam/orthanc:25.12.3",
]


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


def _load_lockfile(lock_path: Path) -> dict[str, str]:
    """Load images.lock. Returns {image: digest}."""
    if not lock_path.exists():
        return {}
    with open(lock_path) as f:
        data = yaml.safe_load(f) or {}
    return dict(data)


def _save_lockfile(lock_path: Path, lock_data: dict[str, str]) -> None:
    """Save images.lock."""
    with open(lock_path, "w") as f:
        yaml.dump(lock_data, f, default_flow_style=False, sort_keys=True)


def pull_and_verify() -> None:
    """Pull locked images and verify digests match images.lock. Update lockfile if missing."""
    project_root = Path.cwd()
    lock_path = project_root / "images.lock"
    lock_data = _load_lockfile(lock_path)

    for image in LOCKED_IMAGES:
        actual = _get_image_digest(image)
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

    _save_lockfile(lock_path, lock_data)
