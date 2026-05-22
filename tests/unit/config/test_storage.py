import os
import sys
import unittest
from importlib import reload
from os import getenv
from unittest.mock import patch

from settings import MEDIA_URL, STATIC_URL


class TestStorage(unittest.TestCase):
    @patch.object(
        sys.modules["__main__"], "__file__", "config/storage.py", create=True
    )
    @patch.dict(os.environ, {"SYSTEM_ENV": "dev"})
    def test_dev(self):
        reload(sys.modules["settings"])
        from settings import MEDIA_URL, STATIC_BASE_URL, STATIC_URL

        # ``STATIC_URL`` stays relative — Django's static-asset
        # pipeline doesn't surface URLs through DRF, so no Zod check
        # punishes it for being relative.
        self.assertEqual(STATIC_URL, "/static/")
        # ``MEDIA_URL`` is intentionally absolute in every environment
        # (including dev) so DRF ``ImageField.url`` always returns a
        # full URL — drf-spectacular emits ``format: 'uri'`` and the
        # storefront's Zod 4 ``z.url()`` schema rejects relative
        # paths, which broke local-dev calls to every endpoint that
        # surfaces uploaded media (PayWay icons, product images,
        # shipping logos, ...). ``STATIC_BASE_URL`` defaults to
        # ``http://localhost:8000``; devs override via env var.
        self.assertEqual(MEDIA_URL, f"{STATIC_BASE_URL}/media/")

    @patch.object(
        sys.modules["__main__"], "__file__", "config/storage.py", create=True
    )
    @patch.dict(
        os.environ,
        {
            "USE_AWS": "True",
            "AWS_STORAGE_BUCKET_NAME": "grooveshop-static",
            "AWS_ACCESS_KEY_ID": "fake_access_key",
            "AWS_SECRET_ACCESS_KEY": "fake_secret_key",
        },
    )
    def test_aws(self):
        reload(sys.modules["settings"])
        from settings import MEDIA_URL, STATIC_URL

        AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME")
        AWS_S3_CUSTOM_DOMAIN = f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"
        PUBLIC_MEDIA_LOCATION = "media"

        self.assertEqual(STATIC_URL, f"https://{AWS_S3_CUSTOM_DOMAIN}/")
        self.assertEqual(
            MEDIA_URL,
            f"https://{AWS_S3_CUSTOM_DOMAIN}/{PUBLIC_MEDIA_LOCATION}/",
        )
