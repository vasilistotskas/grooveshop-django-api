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
