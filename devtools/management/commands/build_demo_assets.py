"""Turn the curated photograph list into the AVIF files the demo ships.

The demo store's images used to be ten powerbank photographs mapped
round-robin onto every product, so a USB-C cable showed a powerbank.
The fix is not more photographs of one thing: it is one photograph per
product FAMILY, checked against what the product actually is.

This command is the only thing that writes ``devtools/demo_assets``.
The files it produces are committed, so a deploy never downloads
anything and a seed run never depends on a stock library being up.
``manifest.lock.json`` records what each file is, which is what lets the
seeder tell "already written" from "changed" without re-encoding.

Dev-only: it needs the network and it writes into the source tree.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from PIL import Image

ASSETS_DIR = Path(__file__).resolve().parents[2] / "demo_assets"
MANIFEST = ASSETS_DIR / "manifest.json"
LOCK = ASSETS_DIR / "manifest.lock.json"
CACHE = ASSETS_DIR / ".cache"

# A product photograph is square because every grid, rail and thumbnail
# on the storefront reserves a square-ish box for it; a category tile is
# a landscape band across the top of its card.
SHAPES: dict[str, tuple[int, int]] = {
    "product": (1200, 1200),
    "category": (1600, 900),
    "blog": (1500, 1000),
    "hero": (2100, 900),
}

# Quality is stepped DOWN until the file fits, rather than fixed: these
# photographs vary from a flat studio background (tiny at any quality)
# to a textured fabric speaker (large), and one quality number for both
# either bloats the repository or ruins the easy ones.
QUALITY_START = 62
QUALITY_FLOOR = 40
QUALITY_STEP = 6
MAX_BYTES = 110 * 1024


@dataclass(frozen=True)
class Asset:
    key: str
    kind: str
    download: str
    source: str
    author: str
    licence: str


def _load_manifest() -> dict[str, Asset]:
    if not MANIFEST.exists():
        raise CommandError(f"No manifest at {MANIFEST}")
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out: dict[str, Asset] = {}
    for key, entry in raw.items():
        kind = entry["kind"]
        if kind not in SHAPES:
            raise CommandError(f"{key}: unknown kind {kind!r}")
        out[key] = Asset(
            key=key,
            kind=kind,
            download=entry["download"],
            source=entry.get("source", ""),
            author=entry.get("author", ""),
            licence=entry.get("licence", ""),
        )
    return out


def _download(asset: Asset, width: int, height: int) -> bytes:
    """Fetch the source frame, asking for at least the size we crop to.

    curl rather than urllib: the CDN answers curl and redirects urllib
    into a 401, and what matters here is the bytes.
    """
    url = asset.download
    joiner = "&" if "?" in url else "?"
    url = f"{url}{joiner}w={width * 2}&h={height * 2}&fit=crop&q=85&fm=jpg"

    cached = CACHE / f"{asset.key}.jpg"
    if cached.exists():
        return cached.read_bytes()

    result = subprocess.run(
        ["curl", "-sSL", "--max-time", "60", url],
        capture_output=True,
        check=True,
    )
    if not result.stdout:
        raise CommandError(f"{asset.key}: empty download from {url}")
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(result.stdout)
    return result.stdout


def _encode(image: Image.Image) -> tuple[bytes, int]:
    quality = QUALITY_START
    while True:
        buffer = io.BytesIO()
        image.save(buffer, format="AVIF", quality=quality)
        data = buffer.getvalue()
        if len(data) <= MAX_BYTES or quality <= QUALITY_FLOOR:
            return data, quality
        quality -= QUALITY_STEP


def _crop(raw: bytes, width: int, height: int) -> Image.Image:
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    target = width / height
    actual = image.width / image.height
    if actual > target:
        new_width = round(image.height * target)
        left = (image.width - new_width) // 2
        image = image.crop((left, 0, left + new_width, image.height))
    elif actual < target:
        new_height = round(image.width / target)
        top = (image.height - new_height) // 2
        image = image.crop((0, top, image.width, top + new_height))
    return image.resize((width, height), Image.LANCZOS)


class Command(BaseCommand):
    help = "Build the committed demo-store image set from its manifest."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            action="append",
            default=[],
            help="Build only these keys (repeatable).",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Verify the committed files match the lock file and exit "
                "non-zero if they do not. Downloads nothing."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-encode even when the lock file says nothing changed.",
        )

    def handle(self, *args, **options):
        assets = _load_manifest()
        only = set(options["only"])
        if only:
            unknown = only - set(assets)
            if unknown:
                raise CommandError(f"Unknown keys: {sorted(unknown)}")
            assets = {k: v for k, v in assets.items() if k in only}

        if options["check"]:
            return self._check(assets)

        lock = (
            json.loads(LOCK.read_text(encoding="utf-8"))
            if LOCK.exists()
            else {}
        )
        written = unchanged = 0

        for key, asset in sorted(assets.items()):
            width, height = SHAPES[asset.kind]
            path = ASSETS_DIR / asset.kind / f"{key}.avif"
            entry = lock.get(key)

            if (
                not options["force"]
                and entry
                and path.exists()
                and path.stat().st_size == entry.get("bytes")
                and entry.get("download") == asset.download
            ):
                unchanged += 1
                continue

            raw = _download(asset, width, height)
            data, quality = _encode(_crop(raw, width, height))

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            lock[key] = {
                "kind": asset.kind,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "width": width,
                "height": height,
                "quality": quality,
                "download": asset.download,
                "source": asset.source,
                "author": asset.author,
                "licence": asset.licence,
            }
            written += 1
            self.stdout.write(
                f"  {key:30} {len(data) // 1024:4} KB  q={quality}  {asset.licence}"
            )

        # Keys removed from the manifest must leave the lock with them,
        # or `--check` guards a file nothing references any more.
        for stale in set(lock) - set(_load_manifest()):
            lock.pop(stale)
            self.stdout.write(f"  dropped from lock: {stale}")

        LOCK.write_text(
            json.dumps(lock, indent=1, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        total = sum(e["bytes"] for e in lock.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"{written} written, {unchanged} unchanged, "
                f"{len(lock)} assets, {total // 1024} KB total"
            )
        )

    def _check(self, assets: dict[str, Asset]) -> None:
        if not LOCK.exists():
            raise CommandError(f"No lock file at {LOCK}")
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        problems: list[str] = []

        for key, asset in sorted(assets.items()):
            entry = lock.get(key)
            if entry is None:
                problems.append(
                    f"{key}: in the manifest, missing from the lock"
                )
                continue
            path = ASSETS_DIR / asset.kind / f"{key}.avif"
            if not path.exists():
                problems.append(f"{key}: {path} is missing")
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry["sha256"]:
                problems.append(f"{key}: {path} does not match the lock")

        for stale in sorted(set(lock) - set(assets)):
            problems.append(f"{stale}: in the lock, missing from the manifest")

        if problems:
            for problem in problems:
                self.stderr.write(self.style.ERROR(f"  {problem}"))
            raise CommandError(f"{len(problems)} asset problem(s)")

        self.stdout.write(
            self.style.SUCCESS(f"{len(assets)} assets match the lock")
        )
