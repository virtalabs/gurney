"""Image pinning and digest verification for reproducibility."""

import hashlib
import subprocess
import urllib.request
from pathlib import Path

import yaml


# Third-party images from testbed.yaml and topology-tapirx-dicom (exclude ${VAR} images)
LOCKED_IMAGES = [
    "postgres:16.6-alpine",
    "redis:7.4-alpine",
    "orthancteam/orthanc:25.12.3",
    "virtalabsinc/blueflow:testbed-3.0.1",
]

# Pcap artifacts: {filename: {"url": str, "sha256": str | None}}
# sha256 None = will be computed on first download and saved
PCAP_ARTIFACTS = {
    "DICOM_C-ECHO-echoscu.pcap": {
        "url": "https://wiki.wireshark.org/uploads/__moin_import__/attachments/SampleCaptures/DICOM_C-ECHO-echoscu.pcap",
        "sha256": None,
    },
}


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


def _build_local_image(
    project_root: Path, dockerfile_name: str, image_tag: str
) -> None:
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


# TODO: Generalize to build_local_image() for all images.
def _build_mock_asset_api(project_root: Path) -> None:
    """Build mock-asset-api:local if Dockerfile exists."""
    _build_local_image(
        project_root, "mock-asset-api.Dockerfile", "mock-asset-api:local"
    )


def _build_tapirx(project_root: Path) -> None:
    """Build tapirx:local if Dockerfile exists (for tapirx-dicom-discovery scenario)."""
    _build_local_image(project_root, "tapirx.Dockerfile", "tapirx:local")


def _build_replay(project_root: Path) -> None:
    """Build replay:local if Dockerfile exists (pcap replay sidecar for tapirx-dicom-discovery)."""
    _build_local_image(project_root, "replay.Dockerfile", "replay:local")


def _fetch_pcap(lock_data: dict, project_root: Path) -> None:
    """Fetch pcap artifacts into replay/pcap/, verify/update sha256 in lock."""
    pcap_dir = project_root / "replay" / "pcap"
    pcap_dir.mkdir(parents=True, exist_ok=True)

    if "pcaps" not in lock_data:
        lock_data["pcaps"] = {}

    for filename, meta in PCAP_ARTIFACTS.items():
        url = meta["url"]
        path = pcap_dir / filename

        if path.exists():
            actual = _sha256_file(path)
            expected = lock_data.get("pcaps", {}).get(filename, {}).get("sha256")
            if expected and actual != expected:
                raise RuntimeError(
                    f"SHA256 mismatch for {filename}\n"
                    f"  expected: {expected}\n  actual:   {actual}"
                )
            print(f"  {filename}: OK")
            continue

        print(f"  Fetching {filename}...")
        urllib.request.urlretrieve(url, path)
        actual = _sha256_file(path)
        lock_data.setdefault("pcaps", {})[filename] = {"url": url, "sha256": actual}
        print(f"  {filename}: {actual}")


def pull_and_verify() -> None:
    """Pull images, build mock-asset-api, fetch pcap, verify. Update lockfile if missing."""
    project_root = Path.cwd()
    lock_path = project_root / "images.lock"
    lock_data = _load_lockfile(lock_path)

    _build_mock_asset_api(project_root)
    _build_tapirx(project_root)
    _build_replay(project_root)

    # Images that may require docker login; skip pull/verify without failing.
    login_required_images = {"virtalabsinc/blueflow:testbed-3.0.0"}

    for image in LOCKED_IMAGES:
        try:
            actual = _get_image_digest(image)
        except (RuntimeError, subprocess.CalledProcessError) as e:
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

    _fetch_pcap(lock_data, project_root)

    _save_lockfile(lock_path, lock_data)
