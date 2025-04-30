import unittest
from unittest.mock import MagicMock, patch

import wsgi


class TestWsgiInit(unittest.TestCase):
    def test_get_allowed_host_lazy(self):
        """Test that get_allowed_host_lazy returns the first allowed host."""
        with patch("django.conf.settings") as mock_settings:
            mock_settings.ALLOWED_HOSTS = ["example.com", "localhost"]

            result = wsgi.get_allowed_host_lazy()

            self.assertEqual(result, "example.com")

    def test_wsgi_application_exists(self):
        """Test that the WSGI application exists."""
        self.assertTrue(hasattr(wsgi, "application"))

    def test_wsgi_environment(self):
        """Test that the WSGI environment contains expected keys."""
        mock_app = MagicMock()
        mock_start_response = MagicMock()

        environ = {
            "REQUEST_METHOD": "GET",
            "SERVER_NAME": "example.com",
            "REMOTE_ADDR": "127.0.0.1",
            "SERVER_PORT": 80,
            "PATH_INFO": "/",
            "wsgi.input": MagicMock(),
            "wsgi.multiprocess": True,
        }

        health_wrapper = wsgi.health_check(mock_app, "/health/")
        health_wrapper(environ, mock_start_response)

        mock_app.assert_called_once_with(environ, mock_start_response)
