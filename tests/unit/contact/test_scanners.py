"""The scanner seam, and the one rule the clamd adapter must not break.

An unparsable answer is not a clean verdict. The whole point of a
scanner is to say "this file is fine", so every path that fails to get
a definite answer has to raise rather than shrug — a bug here is the
kind that silently turns detection off while the admin keeps saying
CLEAN.
"""

from __future__ import annotations

import io
import socket
import threading

import pytest
from django.test import override_settings

from contact.scanners import (
    CLEAN,
    INFECTED,
    SKIPPED,
    AttachmentScannerError,
    AttachmentScannerInterface,
    ClamAvScanner,
    NullScanner,
    get_scanner,
    register_scanner,
)


class TestRegistry:
    def test_the_default_is_the_null_adapter(self):
        with override_settings(CONTACT_ATTACHMENT_SCANNER="null"):
            assert isinstance(get_scanner(), NullScanner)

    def test_a_code_selects_its_adapter(self):
        with override_settings(CONTACT_ATTACHMENT_SCANNER="clamav"):
            assert isinstance(get_scanner(), ClamAvScanner)

    def test_an_unknown_code_falls_back_loudly(self, caplog):
        """A typo in an env var must not take the contact form down."""
        with override_settings(CONTACT_ATTACHMENT_SCANNER="clamvav"):
            scanner = get_scanner()
        assert isinstance(scanner, NullScanner)
        assert "Unknown CONTACT_ATTACHMENT_SCANNER" in caplog.text

    def test_an_empty_setting_falls_back_to_null(self):
        with override_settings(CONTACT_ATTACHMENT_SCANNER=""):
            assert isinstance(get_scanner(), NullScanner)

    def test_an_adapter_must_declare_a_code(self):
        class Nameless(AttachmentScannerInterface):
            def scan(self, stream, *, size):
                return SKIPPED, ""

        with pytest.raises(ValueError, match="must declare a code"):
            register_scanner(Nameless)


class TestNullScanner:
    def test_records_skipped_and_says_why(self):
        verdict, detail = NullScanner().scan(io.BytesIO(b"x"), size=1)
        assert verdict == SKIPPED
        assert "No scanner configured" in detail


class _FakeClamd:
    """A clamd that speaks INSTREAM and answers a canned line.

    A real socket rather than a mock, because the thing worth testing
    is the framing — a 4-byte big-endian length per chunk and a
    zero-length frame to finish — and a mock would only assert the
    calls we already wrote.
    """

    def __init__(self, answer: bytes | None):
        self.answer = answer
        self.received = bytearray()
        self.command = b""
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _ = self._server.accept()
        with conn:
            self.command = self._read_exactly(conn, 10)
            while True:
                header = self._read_exactly(conn, 4)
                if len(header) < 4:
                    return
                length = int.from_bytes(header, "big")
                if length == 0:
                    break
                self.received += self._read_exactly(conn, length)
            if self.answer is not None:
                conn.sendall(self.answer)

    @staticmethod
    def _read_exactly(conn, count: int) -> bytes:
        buf = b""
        while len(buf) < count:
            chunk = conn.recv(count - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def close(self):
        self._server.close()


@pytest.fixture
def clamd(request):
    servers: list[_FakeClamd] = []

    def make(answer: bytes | None):
        server = _FakeClamd(answer)
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.close()


class TestClamAvScanner:
    def _scan(self, server, payload: bytes):
        with override_settings(
            CLAMAV_HOST="127.0.0.1",
            CLAMAV_PORT=server.port,
            CLAMAV_TIMEOUT=5,
        ):
            return ClamAvScanner().scan(io.BytesIO(payload), size=len(payload))

    def test_a_clean_answer(self, clamd):
        server = clamd(b"stream: OK\0")
        verdict, detail = self._scan(server, b"harmless")
        assert verdict == CLEAN
        assert "OK" in detail

    def test_an_infected_answer(self, clamd):
        server = clamd(b"stream: Eicar-Test-Signature FOUND\0")
        verdict, detail = self._scan(server, b"whatever")
        assert verdict == INFECTED
        assert "Eicar-Test-Signature" in detail

    def test_the_command_and_framing(self, clamd):
        """zINSTREAM, then length-prefixed chunks, then a zero frame."""
        server = clamd(b"stream: OK\0")
        payload = b"a" * (ClamAvScanner.CHUNK * 2 + 7)
        self._scan(server, payload)
        assert server.command == b"zINSTREAM\0"
        assert bytes(server.received) == payload

    def test_a_truncated_reply_is_not_clean(self, clamd):
        server = clamd(b"stre")
        with pytest.raises(AttachmentScannerError, match="clamd said"):
            self._scan(server, b"x")

    def test_no_reply_at_all_is_not_clean(self, clamd):
        server = clamd(None)
        with pytest.raises(AttachmentScannerError):
            self._scan(server, b"x")

    def test_an_error_reply_is_not_clean(self, clamd):
        server = clamd(b"INSTREAM size limit exceeded. ERROR\0")
        with pytest.raises(AttachmentScannerError, match="clamd said"):
            self._scan(server, b"x")

    def test_an_unreachable_daemon_raises_rather_than_passing(self):
        """Retryable by the task; never a verdict."""
        # Port 1 on loopback: nothing listens, and the connection is
        # refused rather than timing out.
        with override_settings(
            CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=1, CLAMAV_TIMEOUT=1
        ):
            with pytest.raises(AttachmentScannerError, match="unavailable"):
                ClamAvScanner().scan(io.BytesIO(b"x"), size=1)
