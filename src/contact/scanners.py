"""Malware-scanner adapters for uploaded attachments, and the registry.

The platform's security guarantee for an attachment is CONTAINMENT,
not detection: a private per-tenant tree with no URL, a staff-only
streamed download, `Content-Disposition: attachment` with `nosniff`, a
content-sniffed allow-list, and nothing on the server ever parsing,
rendering or thumbnailing the bytes. That holds whether or not a
scanner is configured, and it is what actually neutralises the attack
an anonymous upload endpoint enables — a file served from the
merchant's own domain.

Scanning is a SEPARATE, optional layer on top, because it is neither
free nor decisive:

* ClamAV needs ~1.6 GB of RAM resident to hold its signature set and
  ~2.4 GB while reloading it, and its own documentation recommends
  3-4 GiB for a server deployment. That is a permanent tax on every
  deployment of this platform, most of which will never enable
  attachments at all.
* Signature matching catches commodity malware and misses anything
  targeted or new. Treating "we scan uploads" as the guarantee, rather
  than containment, is how a system ends up trusting a file because a
  scanner shrugged.

So the interface mirrors ``shipping.interfaces``: an ABC, a registry
populated by a decorator, and a lookup by code. Adding an engine —
clamd on a bigger node, an ICAP appliance, a private cloud API for a
tenant that accepts the egress — is one class and one setting, with no
change to the contact app. What must never be added is a scanner that
ships the bytes to a PUBLIC multi-engine service: those share
submitted samples with their partners, and a tender document is
exactly what may not leave the cluster that way.
"""

from __future__ import annotations

import logging
import socket
from abc import ABC, abstractmethod
from typing import ClassVar

from django.conf import settings

logger = logging.getLogger(__name__)

#: Verdicts an adapter may return. Mirrors
#: ``ContactAttachment.ScanStatus`` minus ``PENDING``, which is the
#: state of a row that has not reached an adapter yet.
CLEAN = "CLEAN"
INFECTED = "INFECTED"
ERROR = "ERROR"
SKIPPED = "SKIPPED"


class AttachmentScannerError(Exception):
    """The engine could not be reached or answered unintelligibly."""


class AttachmentScannerInterface(ABC):
    """Adapter every scanning engine implements.

    One method, deliberately: given the bytes, say what they are. No
    quarantine, no deletion, no notification — the caller
    (``contact.tasks.scan_contact_attachment``) owns what happens to
    the row, so a new engine cannot change the platform's behaviour by
    accident.
    """

    code: ClassVar[str] = ""

    @abstractmethod
    def scan(self, stream, *, size: int) -> tuple[str, str]:
        """Return ``(verdict, detail)``.

        ``verdict`` is one of the module constants. ``detail`` is free
        text stored on the row for the operator — a signature name, an
        engine version, a reason for ``ERROR``. Raising
        ``AttachmentScannerError`` is equivalent to returning
        ``ERROR``; it exists so a transport failure can be retried by
        the task, while a definite verdict is not.
        """
        raise NotImplementedError


_REGISTRY: dict[str, type[AttachmentScannerInterface]] = {}


def register_scanner(cls: type[AttachmentScannerInterface]):
    """Class decorator: make an adapter selectable by its ``code``."""
    if not cls.code:
        raise ValueError(f"{cls.__name__} must declare a code")
    _REGISTRY[cls.code] = cls
    return cls


def get_scanner() -> AttachmentScannerInterface:
    """The configured adapter, or the null one.

    Selected by ``settings.CONTACT_ATTACHMENT_SCANNER`` (env, not a
    merchant setting): which engine the CLUSTER runs is an operator
    decision, not a per-store one — a tenant cannot conjure a ClamAV
    deployment by ticking a box. An unknown code falls back to the null
    adapter with a loud log rather than failing every upload: a typo in
    an env var must not take the contact form down.
    """
    code = getattr(settings, "CONTACT_ATTACHMENT_SCANNER", "null") or "null"
    adapter = _REGISTRY.get(code)
    if adapter is None:
        logger.error(
            "Unknown CONTACT_ATTACHMENT_SCANNER %r; falling back to null. "
            "Known: %s",
            code,
            sorted(_REGISTRY),
        )
        adapter = _REGISTRY["null"]
    return adapter()


@register_scanner
class NullScanner(AttachmentScannerInterface):
    """No engine. Records ``SKIPPED`` and says so.

    The default, and an honest one: the row states that nothing
    scanned the file, so the admin can show it and nobody mistakes an
    unscanned attachment for a cleared one.
    """

    code = "null"

    def scan(self, stream, *, size: int) -> tuple[str, str]:
        return SKIPPED, "No scanner configured (containment only)."


@register_scanner
class ClamAvScanner(AttachmentScannerInterface):
    """clamd over TCP, using ``INSTREAM``.

    Speaks the protocol directly rather than through a client library:
    it is four commands, the platform gains no dependency, and the one
    thing that matters — chunking the stream so a 25 MB file never
    lands in the daemon's memory in one piece — stays visible here.

    ``INSTREAM`` sends ``<4-byte length, network byte order><chunk>``
    frames terminated by a zero-length frame; the daemon answers one
    line ending in ``OK``, ``FOUND`` or ``ERROR``. A stream longer
    than the daemon's ``StreamMaxLength`` gets
    ``INSTREAM size limit exceeded`` and the connection dropped, so
    the caller's size cap must stay at or under it — an operator
    concern, and a mismatch surfaces as an error rather than a false
    ``CLEAN``.

    The command is ``z``-prefixed, which per ``clamd(8)`` means both
    the command AND THE REPLY are NUL-delimited ("clamd replies will
    honour the requested terminator in turn"). That trailing NUL has
    to come off before the verdict is read, or every scan looks
    unparsable — and this adapter turns unparsable into an error, so
    the failure would have been a contact form that never clears a
    file rather than one that wrongly clears them.
    """

    code = "clamav"
    CHUNK = 64 * 1024
    #: A verdict line is short; anything longer is not one.
    MAX_REPLY = 4096

    def scan(self, stream, *, size: int) -> tuple[str, str]:
        host = getattr(settings, "CLAMAV_HOST", "clamav")
        port = int(getattr(settings, "CLAMAV_PORT", 3310))
        timeout = float(getattr(settings, "CLAMAV_TIMEOUT", 30))
        try:
            with socket.create_connection((host, port), timeout) as sock:
                sock.sendall(b"zINSTREAM\0")
                for chunk in iter(lambda: stream.read(self.CHUNK), b""):
                    sock.sendall(
                        len(chunk).to_bytes(4, "big", signed=False) + chunk
                    )
                sock.sendall((0).to_bytes(4, "big", signed=False))
                raw = self._read_reply(sock)
        except OSError as exc:
            raise AttachmentScannerError(
                f"clamd at {host}:{port} unavailable: {exc}"
            ) from exc

        answer = raw.decode("utf-8", "replace").strip("\x00 \r\n\t")
        if answer.endswith("FOUND"):
            return INFECTED, answer
        if answer.endswith("OK"):
            return CLEAN, answer
        # A truncated, empty or unparsable reply is NOT a clean
        # verdict. Raising makes it retryable and, if it persists,
        # ERROR — which is not downloadable.
        raise AttachmentScannerError(f"clamd said: {answer!r}")

    def _read_reply(self, sock) -> bytes:
        """Read until the NUL terminator, EOF, or the reply cap.

        ``recv`` is not a message read: a single call can return a
        fragment, and a reply parsed from a fragment is exactly the
        unparsable case above.
        """
        buffer = b""
        while len(buffer) < self.MAX_REPLY:
            chunk = sock.recv(self.MAX_REPLY - len(buffer))
            if not chunk:
                break
            buffer += chunk
            if b"\x00" in chunk or chunk.endswith(b"\n"):
                break
        return buffer
