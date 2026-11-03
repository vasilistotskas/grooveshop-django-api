"""``manage.py report_unservable_media`` lists, and only lists."""

from __future__ import annotations

import io

from django.core.management import call_command
from PIL import Image

from core import uploads


def png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_it_lists_the_unservable_files_and_deletes_nothing(
    tmp_path, settings, monkeypatch, capsys
):
    settings.MEDIA_ROOT = str(tmp_path)
    (tmp_path / "shop").mkdir()
    ok = tmp_path / "shop" / "ok.png"
    big = tmp_path / "shop" / "big.png"
    ok.write_bytes(png((2, 2)))
    big.write_bytes(png((2, 2)) + b"0" * 5000)
    real = uploads.image_limits()
    monkeypatch.setattr(
        uploads,
        "image_limits",
        lambda: uploads.ImageLimits(
            {**real.max_file_bytes, "png": 1000}, real.source_pixel_limit
        ),
    )
    monkeypatch.setattr(
        "core.management.commands.report_unservable_media.image_limits",
        uploads.image_limits,
    )

    call_command("report_unservable_media")

    out = capsys.readouterr().out
    assert "shop/big.png" in out
    assert "shop/ok.png" not in out
    assert "1 of 2 image files would not be served." in out
    assert ok.exists()
    assert big.exists()
