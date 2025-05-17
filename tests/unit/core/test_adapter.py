import unittest
from unittest.mock import MagicMock, patch

from core.adapter import MFAAdapter


class TestMFAAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = MFAAdapter()
        self.adapter._get_site_name = MagicMock(return_value="Test Site")

    @patch("core.adapter.getenv")
    def test_get_public_key_credential_rp_entity_with_env_var(
        self, mock_getenv
    ):
        mock_getenv.return_value = "example.com"

        result = self.adapter.get_public_key_credential_rp_entity()

        mock_getenv.assert_called_once_with("APP_MAIN_HOST_NAME", "localhost")

        self.assertEqual(
            result,
            {
                "id": "example.com",
                "name": "Test Site",
            },
        )

        self.adapter._get_site_name.assert_called_once()

    @patch("core.adapter.getenv")
    def test_get_public_key_credential_rp_entity_with_default(
        self, mock_getenv
    ):
        mock_getenv.return_value = None

        result = self.adapter.get_public_key_credential_rp_entity()

        mock_getenv.assert_called_once_with("APP_MAIN_HOST_NAME", "localhost")

        self.assertEqual(
            result,
            {
                "id": None,
                "name": "Test Site",
            },
        )

        self.adapter._get_site_name.assert_called_once()
