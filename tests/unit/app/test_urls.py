from django.conf import settings
from django.test import override_settings
from django.test import TestCase


class DebugToolbarURLsTestCase(TestCase):
    @override_settings(DEBUG=False, ENABLE_DEBUG_TOOLBAR=True)
    def test_no_debug_toolbar_url_when_debug_false(self):
        # When DEBUG is False, the debug toolbar URL should not work
        response = self.client.get("/__debug__/", follow=True)
        self.assertEqual(response.status_code, 404)

    def test_static_url(self):
        # Testing a known static file URL
        response = self.client.get(
            f"{settings.STATIC_URL}images/no_photo.jpg", follow=True
        )
        # In DEBUG mode, static files should be served so the response shouldn't be 404
        self.assertNotEqual(response.status_code, 404)
