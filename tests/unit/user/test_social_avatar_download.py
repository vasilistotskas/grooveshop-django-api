"""The social-avatar download goes through ``safe_fetch``, capped and typed."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from PIL import Image

from core.net.safe_fetch import (
    FetchedResponse,
    ResponseTooLargeError,
    UnsafeUrlError,
)
from core.uploads import image_limits
from user.factories.account import UserAccountFactory
from user.tasks import download_social_avatar_task

pytestmark = pytest.mark.django_db

GPS_IFD = 0x8825
URL = "https://lh3.googleusercontent.com/a/avatar"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, "PNG")
    return out.getvalue()


def test_the_avatar_is_fetched_with_the_largest_image_cap_and_saved(settings):
    user = UserAccountFactory(num_addresses=0)

    with patch(
        "user.tasks.safe_fetch",
        return_value=FetchedResponse(URL, _png(), "image/png"),
    ) as fetch:
        download_social_avatar_task.apply(args=[user.pk, URL]).get()

    fetch.assert_called_once_with(
        URL, max_bytes=image_limits().largest_file_bytes
    )
    user.refresh_from_db()
    assert user.image


@pytest.mark.parametrize("error", [UnsafeUrlError, ResponseTooLargeError])
def test_a_refused_avatar_is_not_retried_and_saves_nothing(error):
    user = UserAccountFactory(num_addresses=0)
    before = user.image.name

    with (
        patch("user.tasks.safe_fetch", side_effect=error("refused")),
        patch.object(download_social_avatar_task, "retry") as retry,
        pytest.raises(error),
    ):
        download_social_avatar_task.apply(
            args=[user.pk, "http://169.254.169.254/"], throw=True
        ).get()

    retry.assert_not_called()
    user.refresh_from_db()
    assert user.image.name == before


def _jpeg_with_gps() -> bytes:
    image = Image.new("RGB", (8, 8), "blue")
    exif = Image.Exif()
    gps = exif.get_ifd(GPS_IFD)
    gps[1], gps[2] = "N", (37.0, 58.0, 0.0)
    gps[3], gps[4] = "E", (23.0, 43.0, 0.0)
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_an_avatar_goes_through_the_upload_rule_and_loses_its_gps_position():
    user = UserAccountFactory(num_addresses=0)
    original = _jpeg_with_gps()
    assert Image.open(io.BytesIO(original)).getexif().get_ifd(GPS_IFD)

    with patch(
        "user.tasks.safe_fetch",
        return_value=FetchedResponse(URL, original, "image/jpeg"),
    ):
        download_social_avatar_task.apply(args=[user.pk, URL]).get()

    user.refresh_from_db()
    assert user.image.name.endswith(".jpg")
    with Image.open(user.image) as stored:
        assert not stored.getexif().get_ifd(GPS_IFD)


def test_the_stored_extension_follows_the_format():
    user = UserAccountFactory(num_addresses=0)

    with patch(
        "user.tasks.safe_fetch",
        return_value=FetchedResponse(URL, _png(), "image/png"),
    ):
        download_social_avatar_task.apply(args=[user.pk, URL]).get()

    user.refresh_from_db()
    assert user.image.name.endswith(".png")


@pytest.mark.parametrize(
    "content",
    [b"<html>not an image</html>", b"GIF89a-but-not", b"<svg xmlns='x'/>"],
)
def test_a_file_that_is_not_a_servable_raster_is_dropped(content):
    user = UserAccountFactory(num_addresses=0)
    before = user.image.name

    with patch(
        "user.tasks.safe_fetch",
        return_value=FetchedResponse(URL, content, "image/png"),
    ):
        download_social_avatar_task.apply(args=[user.pk, URL], throw=True).get()

    user.refresh_from_db()
    assert user.image.name == before
