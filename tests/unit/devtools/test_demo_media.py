"""Demo photographs are stored under content-addressed names.

The media service, Cloudflare and browsers cache a processed image for a
year under its URL, so a rebuilt photograph must land at a new name, and
the earlier builds of the same key must not pile up on the volume.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from django.core.files.storage import FileSystemStorage
from PIL import Image

from devtools import demo_media
from devtools.management.commands import build_demo_assets


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def assets(tmp_path, monkeypatch):
    """A one-key asset tree and an empty tenant volume, both on disk."""
    assets_dir = tmp_path / "assets"
    (assets_dir / "product").mkdir(parents=True)
    storage = FileSystemStorage(location=tmp_path / "volume")
    monkeypatch.setattr(demo_media, "ASSETS_DIR", assets_dir)
    monkeypatch.setattr(demo_media, "default_storage", storage)

    def build(key: str, data: bytes) -> None:
        (assets_dir / "product" / f"{key}.avif").write_bytes(data)
        lock = dict(demo_media._lock_cache or {})
        lock[key] = {"kind": "product", "sha256": _digest(data)}
        monkeypatch.setattr(demo_media, "_lock_cache", lock)

    monkeypatch.setattr(demo_media, "_lock_cache", {})
    return build, storage


def _files(storage) -> list[str]:
    _, files = storage.listdir("uploads/products")
    return sorted(files)


def test_the_name_carries_the_content_hash(assets):
    build, _ = assets
    build("cable-coiled", b"first build")

    name = demo_media.storage_name("cable-coiled")

    assert name == (
        f"uploads/products/cable-coiled-{_digest(b'first build')[:12]}.avif"
    )


def test_a_second_seed_writes_nothing(assets):
    build, storage = assets
    build("cable-coiled", b"first build")
    demo_media.ensure_asset("cable-coiled")

    with mock.patch.object(storage, "save") as save:
        demo_media.ensure_asset("cable-coiled")

    save.assert_not_called()


def test_a_rebuilt_photo_gets_a_new_name_and_retires_the_old_ones(assets):
    build, storage = assets
    storage.save("uploads/products/cable-coiled.avif", io.BytesIO(b"legacy"))
    build("cable-coiled", b"first build")
    first = demo_media.ensure_asset("cable-coiled")

    build("cable-coiled", b"second build")
    second = demo_media.ensure_asset("cable-coiled")

    assert second != first
    assert _files(storage) == [Path(second).name]


def test_pruning_leaves_a_key_that_merely_starts_the_same(assets):
    """``cable-usbc-black`` must not delete ``cable-usbc-black-detail``."""
    build, storage = assets
    build("cable-usbc-black-detail", b"detail")
    detail = demo_media.ensure_asset("cable-usbc-black-detail")
    build("cable-usbc-black", b"black")

    black = demo_media.ensure_asset("cable-usbc-black")

    assert _files(storage) == sorted([Path(black).name, Path(detail).name])


def _jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


class TestBuildCommand:
    def test_a_larger_source_is_cropped_and_scaled_to_the_shape(self):
        image = build_demo_assets._crop(_jpeg(4000, 3000), 2400, 2400)

        assert image.size == (2400, 2400)

    def test_a_smaller_source_is_cropped_but_never_enlarged(self):
        image = build_demo_assets._crop(_jpeg(2000, 1500), 2400, 2400)

        assert image.size == (1500, 1500)

    def test_the_download_cache_follows_the_url_not_the_key(
        self, tmp_path, monkeypatch
    ):
        """A key whose photograph is replaced must not reuse the old file."""
        monkeypatch.setattr(build_demo_assets, "CACHE", tmp_path)
        fetched = []

        def fake_run(args, **kwargs):
            fetched.append(args[-1])
            return subprocess.CompletedProcess(
                args, 0, stdout=f"bytes of {args[-1]}".encode()
            )

        monkeypatch.setattr(build_demo_assets.subprocess, "run", fake_run)

        def asset(download: str) -> build_demo_assets.Asset:
            return build_demo_assets.Asset(
                key="cable-coiled",
                kind="product",
                download=download,
                source="",
                author="",
                licence="Unsplash",
            )

        old = build_demo_assets._download(asset("https://x/old"), 10, 10)
        new = build_demo_assets._download(asset("https://x/new"), 10, 10)
        again = build_demo_assets._download(asset("https://x/new"), 10, 10)

        assert old != new
        assert again == new
        assert len(fetched) == 2
