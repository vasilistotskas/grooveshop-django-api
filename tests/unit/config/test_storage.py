import os
import sys
import unittest
from importlib import reload
from os import getenv
from unittest.mock import patch


class TestStorage(unittest.TestCase):
    @patch.object(sys.modules["__main__"], "__file__", "config/storage.py")
    @patch.dict(os.environ, {"SYSTEM_ENV": "dev"})
    def test_dev(self):
        reload(sys.modules["settings"])
        from settings import STATIC_URL, MEDIA_URL

        self.assertEqual(STATIC_URL, "/static/")
        self.assertEqual(MEDIA_URL, "/media/")

    @patch.object(sys.modules["__main__"], "__file__", "config/storage.py")
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
        from settings import STATIC_URL, MEDIA_URL

        AWS_STORAGE_BUCKET_NAME = getenv("AWS_STORAGE_BUCKET_NAME")
        AWS_S3_CUSTOM_DOMAIN = f"{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com"
        PUBLIC_MEDIA_LOCATION = "media"

        self.assertEqual(STATIC_URL, f"https://{AWS_S3_CUSTOM_DOMAIN}/")
        self.assertEqual(
            MEDIA_URL,
            f"https://{AWS_S3_CUSTOM_DOMAIN}/{PUBLIC_MEDIA_LOCATION}/",
        )
