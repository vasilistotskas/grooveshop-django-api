"""What a store accepts as an attachment, and how bytes are checked.

This is the GATE. The rest of the feature is containment
(``contact.storage``: a private per-tenant tree with no URL), an
optional detection layer (``contact.scanners``) and lifecycle
(``contact.tasks``); this module decides what is allowed in at all.

Kept out of ``contact.serializers`` because three callers need it and
only one of them serialises anything: the upload serializer, the
enquiry serializer that claims uploads, and the storefront (through
the public-settings endpoint, which reads the same four keys to decide
whether to render the control).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import timedelta

import filetype
from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone
from extra_settings.models import Setting

logger = logging.getLogger(__name__)

#: How long an uploaded-but-unclaimed file waits for its enquiry. Long
#: enough to write a description and hit send on a slow connection,
#: short enough that abandoned uploads do not sit in the tree. The
#: reaper (``contact.tasks.reap_unclaimed_attachments``) is what
#: enforces it.
CLAIM_WINDOW_HOURS = 6

#: The window the intake ceiling measures over. A day rather than an
#: hour so a genuinely busy tender week is not refused, and rather
#: than a month so a flood is stopped in hours instead of weeks.
INTAKE_WINDOW_HOURS = 24

#: Control characters, which have no business in a stored filename.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")


class AttachmentPolicy:
    """The merchant's attachment limits, read once per request.

    Every value is an ``extra_settings`` row, so a store changes them
    in the admin with no deploy and no code path per tenant — and the
    storefront reads the same four to decide whether to render the
    control at all. The server re-reads them because a client is not a
    gate.

    ``MAX_BYTES_CEILING`` is the one limit a merchant cannot raise: the
    ingress, the SSR proxy and the pod's ephemeral disk are the
    operator's resources, not the store's, and a setting that could
    ask for 2 GB would be a self-inflicted denial of service.
    """

    MAX_BYTES_CEILING = 25 * 1024 * 1024
    #: The same reasoning applied to the count: a store choosing how
    #: many drawings an enquiry may carry is choosing how much of the
    #: operator's volume one submission can occupy. It also gives the
    #: claim list a STRUCTURAL bound (``ListField(max_length=...)``),
    #: which a per-tenant setting cannot — a declared field's limits
    #: are fixed at class definition, before any tenant is bound.
    MAX_COUNT_CEILING = 20
    #: The code default is the narrowest useful one. A store that wants
    #: drawings or archives says so in its own settings; a store that
    #: never thinks about it accepts documents only.
    DEFAULT_TYPES = ("application/pdf",)

    def __init__(self) -> None:
        self.enabled = bool(
            Setting.get("CONTACT_ATTACHMENTS_ENABLED", default=False)
        )
        self.max_count = min(
            max(
                1, int(Setting.get("CONTACT_ATTACHMENTS_MAX_COUNT", default=3))
            ),
            self.MAX_COUNT_CEILING,
        )
        megabytes = max(
            1, int(Setting.get("CONTACT_ATTACHMENTS_MAX_MB", default=10))
        )
        self.max_bytes = min(megabytes * 1024 * 1024, self.MAX_BYTES_CEILING)
        raw = Setting.get("CONTACT_ATTACHMENTS_TYPES", default="") or ""
        types = tuple(
            part.strip().lower() for part in raw.split(",") if part.strip()
        )
        self.allowed_types = types or self.DEFAULT_TYPES

    @property
    def max_megabytes(self) -> int:
        """The effective ceiling in MB, for a message to the visitor."""
        return self.max_bytes // (1024 * 1024)

    def accepts(self, content_type: str | None) -> bool:
        """Whether a SNIFFED type is on this store's allow-list.

        ``None`` — the sniffer could not confirm the format — is never
        accepted. See ``sniff_content_type``.
        """
        return (
            content_type is not None
            and content_type.lower() in self.allowed_types
        )


def sniff_content_type(upload) -> str | None:
    """The type the BYTES claim to be, or ``None`` if unrecognised.

    The filename's extension and the client's ``Content-Type`` header
    are both ignored: they are assertions by the uploader, and this
    endpoint's whole job is to not take an anonymous uploader's word
    for anything.

    ``filetype`` is handed the file OBJECT rather than a slice we read
    ourselves, because how many leading bytes a signature needs is the
    library's business, not ours (it reads 8192 today, and some of its
    matchers inspect a zip's central directory to tell a ``.docx`` from
    a bare archive). It restores the stream position afterwards.

    It confirms CONTAINER formats, not text: PDF, DWG, ZIP and the
    Office/OpenDocument families sniff cleanly, while an ASCII format
    with no magic number — DXF, CSV, plain text — sniffs as ``None``
    and is therefore refused. That is deliberate rather than a gap: a
    text format cannot be confirmed by signature at all, so accepting
    one would mean trusting the extension, and the honest answer for a
    store that must receive DXF is that it travels in a ZIP (which is
    how CAD deliverables travel anyway).
    """
    kind = filetype.guess(upload)
    return kind.mime if kind is not None else None


def sanitize_filename(name: str | None) -> str:
    """The uploader's filename, made safe to STORE and to display.

    Not used as a path — see ``contact.storage`` — so this is about
    what an operator sees in the admin and what the download hands
    back: no directory components, no control characters, bounded
    length, and never empty.

    A name made only of dots (``..``) is treated as empty too. It is
    harmless here for the same reason the rest is — nothing joins it
    to a path — but it is the name a browser cannot save and an
    operator cannot read, so there is nothing to preserve.
    """
    base = os.path.basename((name or "").replace("\\", "/"))
    base = _CONTROL_RE.sub("", base).strip()
    base = _WHITESPACE_RE.sub(" ", base)
    if not base.strip("."):
        return "attachment"
    return base[:255]


def _operator_bytes(name: str, default_mb: int) -> int:
    """An operator ceiling in bytes. Zero means "no ceiling"."""
    return max(0, int(getattr(settings, name, default_mb))) * 1024 * 1024


def storage_pressure(incoming: int) -> str | None:
    """Which per-tenant ceiling this upload would cross, or ``None``.

    Two ceilings, because they answer different questions and one
    without the other is drivable straight through:

    ``unclaimed``
        Bytes held by uploads no enquiry has claimed. Bounds abandoned
        uploads, and is the alarm for a reaper that stopped running.

    ``intake``
        Bytes accepted in the last ``INTAKE_WINDOW_HOURS``, claimed or
        not. This is the one that bounds an ATTACK: claiming an upload
        exempts it from the reaper, so a caller who submits one
        enquiry per batch keeps every byte for the store's whole
        retention window — 500 MB/hour at the per-caller throttle's
        own ceiling, on a volume that also holds the invoices. A RATE
        is what distinguishes a flood from a busy tender week, so a
        rate is what is capped.

    Why ceilings at all when there is already a throttle: the throttle
    is a REQUEST budget and it is per caller. It bounds one visitor,
    not four hundred, and it says nothing about bytes.

    One query, two conditional sums. Logged at ERROR when either
    binds: a store hitting one is under a flood or has a sweep that
    stopped, and an operator has to see that rather than infer it from
    a support ticket.
    """
    from contact.models import ContactAttachment

    unclaimed_budget = _operator_bytes(
        "CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB", 512
    )
    intake_budget = _operator_bytes(
        "CONTACT_ATTACHMENTS_INTAKE_BUDGET_MB", 1024
    )
    if not unclaimed_budget and not intake_budget:
        return None

    cutoff = timezone.now() - timedelta(hours=INTAKE_WINDOW_HOURS)
    held = ContactAttachment.objects.aggregate(
        unclaimed=Sum("size", filter=Q(contact__isnull=True)),
        intake=Sum("size", filter=Q(created_at__gte=cutoff)),
    )
    wanted = max(0, incoming)

    # The name doubles as the aggregate's key and as the word in the
    # log line, so an operator reading the log knows which ceiling
    # bound and therefore what to look at.
    for name, budget, hint in (
        (
            "unclaimed",
            unclaimed_budget,
            "Check that reap_unclaimed_attachments is running.",
        ),
        (
            "intake",
            intake_budget,
            f"Measured over the last {INTAKE_WINDOW_HOURS}h.",
        ),
    ):
        if not budget:
            continue
        already = held[name] or 0
        if already + wanted > budget:
            logger.error(
                "Contact attachment %s ceiling reached: %s bytes against a "
                "%s byte budget; refusing a %s byte upload. %s",
                name,
                already,
                budget,
                incoming,
                hint,
            )
            return name
    return None
