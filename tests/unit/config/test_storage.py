import os
import sys
import unittest
from importlib import reload
from unittest.mock import patch

# Each test reloads the ``settings`` module inside an
# ``os.environ`` patch and re-imports the URL constants — the
# module-level re-import is intentionally avoided so the values
# reflect the patched env, not whatever was first loaded at
# pytest collection time.


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
