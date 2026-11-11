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
#
# Sizes are twice the largest box each kind is drawn in, so a retina or
# phone screen (2x, 3x) gets real pixels instead of a browser upscale;
# the media service resizes down per request. A source smaller than its
# shape is kept at its own size — upscaling only adds blur.
SHAPES: dict[str, tuple[int, int]] = {
    "product": (2400, 2400),
    "category": (3200, 1800),
    "blog": (3000, 2000),
    "hero": (4200, 1800),
}

# Quality is stepped DOWN until the file fits, rather than fixed: these
# photographs vary from a flat studio background (tiny at any quality)
# to a textured fabric speaker (large), and one quality number for both
# either bloats the repository or ruins the easy ones. Full-resolution
# chroma (4:4:4): 4:2:0 smears the thin coloured edges product shots are
# made of — cable sleeves, port outlines, printed text.
QUALITY_START = 80
QUALITY_FLOOR = 56
QUALITY_STEP = 6
MAX_BYTES = 350 * 1024
SUBSAMPLING = "4:4:4"


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
    # `fit=min`, not `fit=crop`: both crop to the shape's aspect ratio,
    # but `crop` enlarges a smaller original to fill the box and `min`
    # never exceeds the original's own pixels.
    url = f"{url}{joiner}w={width}&h={height}&fit=min&q=90&fm=jpg"

    # Keyed by the URL, not the asset key: replacing a key's photograph
    # changes its download, and a key-named cache would hand back the
    # old frame.
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    cached = CACHE / f"{asset.key}-{digest}.jpg"
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
        image.save(
            buffer, format="AVIF", quality=quality, subsampling=SUBSAMPLING
        )
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
    if image.width <= width:
        return image
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
            image = _crop(raw, width, height)
            width, height = image.size
            data, quality = _encode(image)

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
