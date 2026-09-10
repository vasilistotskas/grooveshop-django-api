import unittest
from unittest.mock import patch

from core.adapter import MFAAdapter


class TestMFAAdapter(unittest.TestCase):
    """The passkey relying party is anchored to the ACTIVE store: its id
    is the store's primary domain (the platform host without a store)
    and its name the store's display name — never the Site framework's
    current site, which on a multi-tenant deployment is one store's
    name shown to every other store's users."""

    def setUp(self):
        self.adapter = MFAAdapter()

    @patch("core.adapter.tenant_site_name", return_value="Test Site")
    @patch("core.adapter.settings")
    def test_rp_entity_uses_platform_host_without_a_tenant(
        self, mock_settings, _site_name
    ):
        mock_settings.APP_MAIN_HOST_NAME = "example.com"

        result = self.adapter.get_public_key_credential_rp_entity()

        self.assertEqual(result, {"id": "example.com", "name": "Test Site"})

    @patch("core.adapter.tenant_site_name", return_value="Test Site")
    @patch("core.adapter.settings")
    def test_rp_entity_falls_back_to_localhost_for_tests(
        self, mock_settings, _site_name
    ):
        del mock_settings.APP_MAIN_HOST_NAME

        result = self.adapter.get_public_key_credential_rp_entity()

        self.assertEqual(result, {"id": "localhost", "name": "Test Site"})
