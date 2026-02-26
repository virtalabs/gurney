"""Tests for reproducibility config loading and artifact sync/fetch behavior."""

from pathlib import Path

import pytest

import testbed.reproduce as reproduce
from testbed.reproduce import (
    ArtifactSpec,
    _load_config,
    _load_topology_artifacts,
    _sha256_file,
    _sync_or_fetch_artifacts,
)


def test_sync_or_fetch_artifacts_prefers_fixture_and_syncs_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixture file is source-of-truth and gets synced into cache."""
    artifacts = (
        ArtifactSpec(filename="sample.pcap", url="https://example.invalid/sample.pcap"),
    )

    fixture = tmp_path / "topologies" / "demo" / "fixtures" / "pcap" / "sample.pcap"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"fixture-content")

    cache = tmp_path / "var" / "artifacts" / "sample.pcap"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stale-content")

    lock_data: dict = {}
    _sync_or_fetch_artifacts(lock_data, tmp_path, "demo", artifacts)

    assert cache.read_bytes() == b"fixture-content"
    expected = _sha256_file(fixture)
    assert lock_data["pcaps"]["sample.pcap"]["sha256"] == expected


def test_sync_or_fetch_artifacts_uses_cache_when_fixture_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache file is accepted when fixture is absent and hash matches lock (or lock unset)."""
    artifacts = (
        ArtifactSpec(filename="sample.pcap", url="https://example.invalid/sample.pcap"),
    )

    cache = tmp_path / "var" / "artifacts" / "sample.pcap"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"cache-content")

    def _unexpected_download(_url: str, _path: Path) -> None:
        raise AssertionError("urlretrieve should not be called when cache exists")

    monkeypatch.setattr(reproduce.urllib.request, "urlretrieve", _unexpected_download)
    lock_data: dict = {}
    _sync_or_fetch_artifacts(lock_data, tmp_path, "demo", artifacts)
    assert lock_data["pcaps"]["sample.pcap"]["sha256"] == _sha256_file(cache)


def test_sync_or_fetch_artifacts_downloads_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing artifact is downloaded into cache and lock is updated."""
    artifacts = (
        ArtifactSpec(filename="sample.pcap", url="https://example.invalid/sample.pcap"),
    )

    def _fake_download(_url: str, path: Path) -> None:
        Path(path).write_bytes(b"downloaded-content")

    monkeypatch.setattr(reproduce.urllib.request, "urlretrieve", _fake_download)
    lock_data: dict = {}
    _sync_or_fetch_artifacts(lock_data, tmp_path, "demo", artifacts)

    cache = tmp_path / "var" / "artifacts" / "sample.pcap"
    assert cache.exists()
    assert lock_data["pcaps"]["sample.pcap"]["sha256"] == _sha256_file(cache)


def test_sync_or_fetch_artifacts_raises_on_fixture_hash_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock hash mismatch against fixture fails fast with fixture path context."""
    artifacts = (
        ArtifactSpec(filename="sample.pcap", url="https://example.invalid/sample.pcap"),
    )

    fixture = tmp_path / "topologies" / "demo" / "fixtures" / "pcap" / "sample.pcap"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"fixture-content")

    lock_data = {"pcaps": {"sample.pcap": {"sha256": "bad-hash"}}}
    with pytest.raises(RuntimeError) as exc:
        _sync_or_fetch_artifacts(lock_data, tmp_path, "demo", artifacts)
    assert "SHA256 mismatch for fixture sample.pcap" in str(exc.value)
    assert str(fixture) in str(exc.value)


def test_load_config_from_topology_root_lowercase(tmp_path: Path) -> None:
    """Loader reads topologies/<id>/config.yaml and parses expected fields."""
    config_path = tmp_path / "topologies" / "demo" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "locked_images: ['img:1']\n"
        "local_images:\n"
        "  - dockerfile: replay.Dockerfile\n"
        "    tag: replay:local\n"
        "login_required_images: []\n",
        encoding="utf-8",
    )
    loaded = _load_config(tmp_path, "demo")
    assert loaded.locked_images == ("img:1",)
    assert loaded.local_images[0].dockerfile == "replay.Dockerfile"
    assert loaded.login_required_images == ()


def test_load_config_missing_file_uses_lowercase_name_in_error(tmp_path: Path) -> None:
    """Missing config reports lowercase config.yaml path and naming."""
    with pytest.raises(RuntimeError) as exc:
        _load_config(tmp_path, "demo")
    msg = str(exc.value)
    assert "topologies" in msg and "demo" in msg and "config.yaml" in msg


def test_load_config_validation_missing_keys(tmp_path: Path) -> None:
    """Loader fails fast when required keys are absent."""
    config_path = tmp_path / "topologies" / "demo" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("locked_images: ['img:1']\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        _load_config(tmp_path, "demo")
    assert "missing required keys" in str(exc.value)


def test_load_config_rejects_unknown_keys(tmp_path: Path) -> None:
    """Loader rejects arbitrary unknown keys in config.yaml."""
    config_path = tmp_path / "topologies" / "demo" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "locked_images: []\n"
        "local_images: []\n"
        "login_required_images: []\n"
        "fixture_pcap_root: topologies/demo/fixtures/pcap\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError) as exc:
        _load_config(tmp_path, "demo")
    assert "unknown keys" in str(exc.value)


def test_load_topology_artifacts_from_topology_yaml(tmp_path: Path) -> None:
    """Topology artifact catalog is loaded from topologies/<id>/topology.yaml."""
    topology_path = tmp_path / "topologies" / "demo" / "topology.yaml"
    topology_path.parent.mkdir(parents=True)
    topology_path.write_text(
        "networks:\n"
        "  - name: net1\n"
        "    cidr: 192.168.10.0/24\n"
        "artifacts:\n"
        "  - id: sample\n"
        "    kind: pcap\n"
        "    filename: sample.pcap\n"
        "    url: https://example.invalid/sample.pcap\n"
        "nodes: []\n",
        encoding="utf-8",
    )
    loaded = _load_topology_artifacts(tmp_path, "demo")
    assert len(loaded) == 1
    assert loaded[0].filename == "sample.pcap"
