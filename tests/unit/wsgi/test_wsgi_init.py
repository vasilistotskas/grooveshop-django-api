import unittest
from unittest.mock import MagicMock

import wsgi


class TestWsgiInit(unittest.TestCase):
    def test_wsgi_application_exists(self):
        self.assertTrue(hasattr(wsgi, "application"))

    def test_wsgi_environment(self):
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
