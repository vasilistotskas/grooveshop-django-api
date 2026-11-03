"""List the stored images the media service would not serve.

The media service shows a placeholder for a source above its per-format byte
cap or pixel limit (``core/data/image-limits.json``), and an upload is checked
against that when it is made (``core.uploads``). Files stored BEFORE the check
existed can still be over a cap; this finds them. Read-only: it deletes,
moves and re-encodes nothing, because what to do with a store's file is the
store's owner's decision.

    uv run python manage.py report_unservable_media
    uv run python manage.py report_unservable_media --schema webside
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.uploads import image_limits, stored_image_problem

#: File extensions of the formats the media service takes (the content, not
#: the name, decides the format; a name only chooses what is looked at).
IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".avif", ".svg"}
)


class Command(BaseCommand):
    help = (
        "List the images in the media directory that the media service would "
        "not serve (over its size cap or pixel limit). Changes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            help="Only this store's directory (default: every store).",
        )

    def handle(self, *args, **options):
        root = Path(settings.MEDIA_ROOT)
        limits = image_limits()
        self.stdout.write(
            "Caps (MiB): "
            + ", ".join(
                f"{name} {size / (1024 * 1024):g}"
                for name, size in sorted(limits.max_file_bytes.items())
            )
            + f"; source pixels {limits.source_pixel_limit}"
        )
        scope = root / options["schema"] if options["schema"] else root
        checked = 0
        found: list[tuple[str, str]] = []
        for path in sorted(scope.rglob("*")):
            if (
                path.suffix.lower() not in IMAGE_EXTENSIONS
                or not path.is_file()
            ):
                continue
            checked += 1
            problem = stored_image_problem(path)
            if problem is not None:
                found.append((path.relative_to(root).as_posix(), problem))
        for relative, problem in found:
            self.stdout.write(f"{relative}: {problem}")
        self.stdout.write(
            f"{len(found)} of {checked} image files would not be served."
        )
