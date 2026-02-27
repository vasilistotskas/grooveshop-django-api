import unittest
from unittest.mock import MagicMock, patch

from core.adapter import MFAAdapter


class TestMFAAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = MFAAdapter()
        self.adapter._get_site_name = MagicMock(return_value="Test Site")

    @patch("core.adapter.settings")
    def test_get_public_key_credential_rp_entity_with_env_var(
        self, mock_settings
    ):
        mock_settings.APP_MAIN_HOST_NAME = "example.com"

        result = self.adapter.get_public_key_credential_rp_entity()

        self.assertEqual(
            result,
            {
                "id": "example.com",
                "name": "Test Site",
            },
        )

        self.adapter._get_site_name.assert_called_once()

    @patch("core.adapter.settings")
    def test_get_public_key_credential_rp_entity_with_default(
        self, mock_settings
    ):
        del mock_settings.APP_MAIN_HOST_NAME

        result = self.adapter.get_public_key_credential_rp_entity()

        self.assertEqual(
            result,
            {
                "id": "localhost",
                "name": "Test Site",
            },
        )

        self.adapter._get_site_name.assert_called_once()
