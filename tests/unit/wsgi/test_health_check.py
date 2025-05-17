import unittest
from unittest.mock import Mock

from wsgi.health_check import health_check


class TestHealthCheck(unittest.TestCase):
    def setUp(self):
        self.mock_app = Mock()
        self.mock_app.return_value = [b"app response"]
        self.health_url = "/health/"
        self.wrapped_app = health_check(self.mock_app, self.health_url)
        self.mock_start_response = Mock()

    def test_health_check_path(self):
        environ = {"PATH_INFO": self.health_url}
        response = self.wrapped_app(environ, self.mock_start_response)

        self.assertEqual(response, [])

        self.mock_start_response.assert_called_once_with(
            "200 OK", [("Content-Type", "text/plain")]
        )

        self.mock_app.assert_not_called()

    def test_non_health_check_path(self):
        environ = {"PATH_INFO": "/some/other/path/"}
        response = self.wrapped_app(environ, self.mock_start_response)

        self.assertEqual(response, [b"app response"])

        self.mock_app.assert_called_once_with(environ, self.mock_start_response)

        self.mock_start_response.assert_not_called()
