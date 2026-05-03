"""C17 — CorrelationIdMiddleware must sanitise control characters."""

from django.test import RequestFactory, TestCase

from core.middleware.correlation_id import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    get_correlation_id,
)


def _make_middleware(response_value=None):
    """Return a middleware instance wrapping a trivial get_response."""
    from django.http import HttpResponse

    def get_response(request):
        return response_value or HttpResponse("ok")

    return CorrelationIdMiddleware(get_response)


class CorrelationIdSanitisationTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _call(self, header_value):
        middleware = _make_middleware()
        request = self.factory.get("/")
        request.META["HTTP_X_CORRELATION_ID"] = header_value
        response = middleware(request)
        return request.correlation_id, response[CORRELATION_ID_HEADER]

    def test_newline_stripped(self):
        stored, echoed = self._call("foo\nbar")
        self.assertEqual(stored, "foobar")
        self.assertEqual(echoed, "foobar")

    def test_carriage_return_stripped(self):
        stored, _ = self._call("foo\rbar")
        self.assertEqual(stored, "foobar")

    def test_null_byte_stripped(self):
        stored, _ = self._call("foo\x00bar")
        self.assertEqual(stored, "foobar")

    def test_del_stripped(self):
        stored, _ = self._call("foo\x7fbar")
        self.assertEqual(stored, "foobar")

    def test_clean_id_preserved(self):
        stored, _ = self._call("abc-123_XYZ")
        self.assertEqual(stored, "abc-123_XYZ")

    def test_truncated_to_64_chars(self):
        long_id = "a" * 100
        stored, _ = self._call(long_id)
        self.assertEqual(len(stored), 64)

    def test_control_chars_stripped_before_truncation(self):
        # 100 chars of "a\n" pairs → after stripping \n = 50 a's, all < 64
        polluted = "a\n" * 50
        stored, _ = self._call(polluted)
        self.assertEqual(stored, "a" * 50)

    def test_missing_header_generates_uuid(self):
        middleware = _make_middleware()

        request = self.factory.get("/")
        # No HTTP_X_CORRELATION_ID in META
        middleware(request)
        stored = request.correlation_id
        # UUID4 hex is 32 lowercase hex chars
        self.assertEqual(len(stored), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in stored))

    def test_context_var_is_restored_after_request(self):
        """After the request the ContextVar must be reset to the sentinel."""
        middleware = _make_middleware()
        request = self.factory.get("/")
        request.META["HTTP_X_CORRELATION_ID"] = "test-id"
        middleware(request)
        # After the call the ContextVar must have been reset
        self.assertEqual(get_correlation_id(), "-")
