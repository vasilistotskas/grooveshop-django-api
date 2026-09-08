"""The upload endpoint and the claim, end to end.

This is the platform's only ANONYMOUS upload, so the assertions here
are mostly about what is REFUSED, and about the two-step handover: an
upload returns a capability (its uuid), the submit spends it once, and
nothing else can.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock
from uuid import uuid4

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from contact.attachments import INTAKE_WINDOW_HOURS, AttachmentPolicy
from contact.models import Contact, ContactAttachment
from core.api.throttling import ContactAttachmentThrottle
from tests.unit.contact.conftest import DWG_HEAD, ZIP_HEAD, pdf_bytes

pytestmark = pytest.mark.django_db

UPLOAD_URL = "/api/v1/contact/attachment"
CONTACT_URL = "/api/v1/contact"

ENQUIRY = {
    "name": "Maria Papadopoulou",
    "email": "maria@example.com",
    "message": "Please quote the attached drawings for the Volos site.",
}


def _upload(client, payload: bytes, name: str = "plan.pdf"):
    return client.post(
        UPLOAD_URL,
        data={"file": SimpleUploadedFile(name, payload)},
        format="multipart",
    )


@pytest.fixture
def client():
    return APIClient()


class TestTheGate:
    def test_off_by_default_the_route_does_not_exist(
        self, client, private_tree
    ):
        """Fails CLOSED: a store that never asked for it hosts nothing."""
        response = _upload(client, pdf_bytes())
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert ContactAttachment.objects.count() == 0

    def test_an_allowed_file_is_accepted_anonymously(
        self, client, private_tree, attachments_on
    ):
        response = _upload(client, pdf_bytes(2048), name="Σχέδιο Α1.pdf")

        assert response.status_code == status.HTTP_201_CREATED, response.data
        row = ContactAttachment.objects.get()
        assert response.data["uuid"] == str(row.uuid)
        # The uploader's name is DATA...
        assert row.original_name == "Σχέδιο Α1.pdf"
        # ...and never the path.
        assert "Σχέδιο" not in row.file.name
        assert str(row.uuid) in row.file.name
        assert row.size == 2048
        assert row.content_type == "application/pdf"
        assert len(row.checksum) == 64
        assert row.contact_id is None
        assert row.claim_deadline > timezone.now()

    def test_the_bytes_land_in_the_private_tree(
        self, client, private_tree, attachments_on
    ):
        _upload(client, pdf_bytes())
        stored = list(private_tree.rglob("*.pdf"))
        assert len(stored) == 1
        assert stored[0].read_bytes() == pdf_bytes()

    def test_the_response_never_carries_the_file_back(
        self, client, private_tree, attachments_on
    ):
        response = _upload(client, pdf_bytes())
        assert "file" not in response.data

    def test_a_type_the_store_did_not_allow(
        self, client, private_tree, attachments_on
    ):
        response = _upload(client, ZIP_HEAD, name="drawings.pdf")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert ContactAttachment.objects.count() == 0

    def test_a_type_the_sniffer_cannot_confirm(
        self, client, private_tree, attachments_on
    ):
        """ASCII DXF has no signature, so it is refused, not trusted."""
        response = _upload(
            client, b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n", name="plan.dxf"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_store_can_widen_the_list_without_a_deploy(
        self, client, private_tree, attachments_on
    ):
        Setting.objects.filter(name="CONTACT_ATTACHMENTS_TYPES").update(
            value_string="application/pdf,image/vnd.dwg"
        )
        response = _upload(client, DWG_HEAD, name="site.dwg")
        assert response.status_code == status.HTTP_201_CREATED
        assert ContactAttachment.objects.get().content_type == "image/vnd.dwg"

    def test_a_file_over_the_stores_limit(
        self, client, private_tree, attachments_on
    ):
        response = _upload(client, pdf_bytes(2 * 1024 * 1024))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "1 MB" in str(response.data)
        assert ContactAttachment.objects.count() == 0

    def test_an_empty_file(self, client, private_tree, attachments_on):
        response = _upload(client, b"")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_no_file_at_all(self, client, private_tree, attachments_on):
        response = client.post(UPLOAD_URL, data={}, format="multipart")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestTheClaim:
    def _uploaded(self, client, count=1):
        ids = []
        for index in range(count):
            response = _upload(client, pdf_bytes(256 + index), f"p{index}.pdf")
            assert response.status_code == status.HTTP_201_CREATED
            ids.append(response.data["uuid"])
        return ids

    def test_an_enquiry_claims_its_uploads(
        self, client, private_tree, attachments_on
    ):
        ids = self._uploaded(client, 2)

        response = client.post(
            CONTACT_URL, {**ENQUIRY, "attachmentIds": ids}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED, response.data
        contact = Contact.objects.get()
        assert contact.attachments.count() == 2
        assert not ContactAttachment.objects.filter(
            contact__isnull=True
        ).exists()

    def test_the_ids_are_never_echoed_back(
        self, client, private_tree, attachments_on
    ):
        ids = self._uploaded(client)
        response = client.post(
            CONTACT_URL, {**ENQUIRY, "attachmentIds": ids}, format="json"
        )
        assert "attachmentIds" not in response.data
        assert "attachment_ids" not in response.data

    def test_an_enquiry_with_no_attachments_still_works(
        self, client, private_tree, attachments_on
    ):
        response = client.post(CONTACT_URL, ENQUIRY, format="json")
        assert response.status_code == status.HTTP_201_CREATED

    def test_an_empty_list_is_accepted(
        self, client, private_tree, attachments_on
    ):
        response = client.post(
            CONTACT_URL, {**ENQUIRY, "attachmentIds": []}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.parametrize("mangle", ["reuse", "expire", "unknown"])
    def test_an_id_that_is_not_claimable_is_refused_identically(
        self, client, private_tree, attachments_on, mangle
    ):
        """One message for every failure mode, on purpose.

        A stranger poking at ids must not learn whether one exists,
        whether it is already claimed, or whether it expired.
        """
        ids = self._uploaded(client)
        if mangle == "reuse":
            client.post(
                CONTACT_URL, {**ENQUIRY, "attachmentIds": ids}, format="json"
            )
        elif mangle == "expire":
            ContactAttachment.objects.update(
                claim_deadline=timezone.now() - timedelta(minutes=1)
            )
        else:
            ids = [str(uuid4())]

        response = client.post(
            CONTACT_URL,
            {**ENQUIRY, "email": "other@example.com", "attachmentIds": ids},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "no longer available" in str(response.data)

    def test_more_files_than_the_store_allows(
        self, client, private_tree, attachments_on
    ):
        Setting.objects.filter(name="CONTACT_ATTACHMENTS_MAX_COUNT").update(
            value_int=2
        )
        ids = self._uploaded(client, 3)

        response = client.post(
            CONTACT_URL, {**ENQUIRY, "attachmentIds": ids}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "At most 2" in str(response.data)

    def test_the_same_id_twice(self, client, private_tree, attachments_on):
        ids = self._uploaded(client)
        response = client.post(
            CONTACT_URL,
            {**ENQUIRY, "attachmentIds": ids * 2},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_ids_are_refused_outright_once_the_store_turns_them_off(
        self, client, private_tree, attachments_on
    ):
        ids = self._uploaded(client)
        Setting.objects.filter(name="CONTACT_ATTACHMENTS_ENABLED").update(
            value_bool=False
        )

        response = client.post(
            CONTACT_URL, {**ENQUIRY, "attachmentIds": ids}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_failed_claim_takes_the_enquiry_with_it(
        self, client, private_tree, attachments_on
    ):
        """The claim and the insert are one transaction.

        Simulates the race: the row is claimed between validation and
        the conditional UPDATE, so the update matches nothing.
        """
        ids = self._uploaded(client)
        other = Contact.objects.create(
            name="First", email="first@example.com", message="x" * 20
        )

        from contact.serializers import ContactWriteSerializer

        serializer = ContactWriteSerializer(
            data={**ENQUIRY, "attachment_ids": ids}
        )
        assert serializer.is_valid(), serializer.errors
        # Somebody else claims it after validation passed.
        ContactAttachment.objects.update(contact=other)

        from rest_framework import serializers as drf

        with pytest.raises(drf.ValidationError):
            serializer.save()
        assert not Contact.objects.filter(email=ENQUIRY["email"]).exists(), (
            "the enquiry must not survive a failed claim"
        )

    def test_the_claim_commits_before_the_notification_is_queued(
        self, client, private_tree, attachments_on
    ):
        """The email task must be able to see the attachments.

        ``contact.signals`` queues it through ``dispatch_on_commit``,
        which fires after the transaction ``create`` opens around the
        insert AND the claim — so by the time the worker loads the
        row, its attachments are there. (What the email then SAYS is
        asserted in ``test_attachment_tasks``: this suite runs
        ``on_commit`` callbacks immediately, so the ordering the
        deferral buys cannot be observed here.)
        """
        ids = self._uploaded(client)
        with mock.patch("tenant.celery.dispatch_on_commit") as queued:
            client.post(
                CONTACT_URL, {**ENQUIRY, "attachmentIds": ids}, format="json"
            )

        queued.assert_called_once()
        contact_id = queued.call_args.args[1][0]
        assert Contact.objects.get(pk=contact_id).attachments.count() == 1


class TestTheThrottle:
    RATE = 3

    @pytest.fixture
    def budget(self, monkeypatch):
        """A rate and a PRIVATE cache for this test only.

        ``SimpleRateThrottle.THROTTLE_RATES`` is bound at import time
        and the suite resolves every rate to ``None``; ``rate`` is the
        documented per-class escape hatch. The cache is local because
        the default one is a Redis shared by every xdist worker, and a
        sibling clearing it mid-loop makes the budget never bind.
        """
        monkeypatch.setattr(
            ContactAttachmentThrottle,
            "rate",
            f"{self.RATE}/hour",
            raising=False,
        )
        monkeypatch.setattr(
            ContactAttachmentThrottle,
            "cache",
            LocMemCache(f"contact-att-{uuid4()}", {}),
            raising=False,
        )

    def test_an_anonymous_uploader_runs_out(
        self, client, private_tree, attachments_on, budget
    ):
        codes = [
            _upload(client, pdf_bytes(), f"p{index}.pdf").status_code
            for index in range(self.RATE + 2)
        ]

        assert codes.count(status.HTTP_429_TOO_MANY_REQUESTS) == 2, codes
        assert ContactAttachment.objects.count() == self.RATE


class TestTheCeilings:
    """The store's BYTES are capped, not just each caller's requests.

    The throttle bounds one visitor's request count. It does not bound
    four hundred of them and it says nothing about bytes — and the
    private tree is a shared volume that also holds the invoices, so a
    flood has to be refused rather than allowed to fill it.
    """

    def test_a_declared_size_no_store_could_accept_is_413(
        self, client, private_tree, attachments_on
    ):
        """Refused BEFORE the parser writes a second copy to disk.

        Touching ``request.data`` runs Django's multipart parser,
        which spools the body again. A refusal that arrives after that
        has already cost what accepting would have cost.
        """
        response = client.post(
            UPLOAD_URL,
            data={"file": SimpleUploadedFile("x.pdf", pdf_bytes(64))},
            format="multipart",
            # The DECLARED length, which is what the check reads.
            headers={"content-length": str(200 * 1024 * 1024)},
        )

        assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        assert ContactAttachment.objects.count() == 0

    def test_a_full_unclaimed_buffer_refuses_with_503_and_stores_nothing(
        self, client, private_tree, attachments_on, settings
    ):
        settings.CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB = 1
        # Already holding more than the budget, unclaimed.
        ContactAttachment.objects.create(
            original_name="held.pdf",
            content_type="application/pdf",
            size=2 * 1024 * 1024,
            checksum="0" * 64,
            claim_deadline=timezone.now() + timedelta(hours=6),
        )

        response = _upload(client, pdf_bytes(1024))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "try again" in str(response.data).lower()
        assert ContactAttachment.objects.count() == 1
        assert list(private_tree.rglob("*.pdf")) == []

    def test_claimed_bytes_do_not_count_against_the_unclaimed_ceiling(
        self, client, private_tree, attachments_on, settings
    ):
        """That ceiling is on the WAITING ROOM, not the archive."""
        settings.CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB = 1
        settings.CONTACT_ATTACHMENTS_INTAKE_BUDGET_MB = 0
        contact = Contact.objects.create(
            name="Past", email="past@example.com", message="x" * 20
        )
        ContactAttachment.objects.create(
            contact=contact,
            original_name="archived.pdf",
            content_type="application/pdf",
            size=50 * 1024 * 1024,
            checksum="0" * 64,
            claim_deadline=timezone.now(),
        )

        response = _upload(client, pdf_bytes(1024))

        assert response.status_code == status.HTTP_201_CREATED

    def test_a_zero_budget_disables_the_ceiling(
        self, client, private_tree, attachments_on, settings
    ):
        settings.CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB = 0
        settings.CONTACT_ATTACHMENTS_INTAKE_BUDGET_MB = 0
        ContactAttachment.objects.create(
            original_name="held.pdf",
            content_type="application/pdf",
            size=500 * 1024 * 1024,
            checksum="0" * 64,
            claim_deadline=timezone.now() + timedelta(hours=6),
        )

        response = _upload(client, pdf_bytes(1024))

        assert response.status_code == status.HTTP_201_CREATED

    def test_the_operator_is_told_to_check_the_reaper(
        self, client, private_tree, attachments_on, settings, caplog
    ):
        settings.CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB = 1
        ContactAttachment.objects.create(
            original_name="held.pdf",
            content_type="application/pdf",
            size=2 * 1024 * 1024,
            checksum="0" * 64,
            claim_deadline=timezone.now() + timedelta(hours=6),
        )

        _upload(client, pdf_bytes(1024))

        assert "unclaimed ceiling reached" in caplog.text
        assert "reap_unclaimed_attachments" in caplog.text

    def test_claimed_bytes_do_count_against_the_intake_ceiling(
        self, client, private_tree, attachments_on, settings
    ):
        """The ceiling that actually bounds an attack.

        Claiming an upload exempts it from the reaper, so a caller who
        submits one enquiry per batch keeps every byte for the store's
        whole retention window. Intake is therefore measured over a
        rolling window REGARDLESS of claim state: a rate is what
        distinguishes a flood from a busy tender week.
        """
        settings.CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB = 0
        settings.CONTACT_ATTACHMENTS_INTAKE_BUDGET_MB = 1
        contact = Contact.objects.create(
            name="Flood", email="flood@example.com", message="x" * 20
        )
        ContactAttachment.objects.create(
            contact=contact,
            original_name="claimed.pdf",
            content_type="application/pdf",
            size=2 * 1024 * 1024,
            checksum="0" * 64,
            claim_deadline=timezone.now(),
        )

        response = _upload(client, pdf_bytes(1024))

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert ContactAttachment.objects.count() == 1

    def test_the_intake_ceiling_only_looks_at_the_recent_window(
        self, client, private_tree, attachments_on, settings
    ):
        """Yesterday's tenders are not this morning's flood."""
        settings.CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB = 0
        settings.CONTACT_ATTACHMENTS_INTAKE_BUDGET_MB = 1
        contact = Contact.objects.create(
            name="Past", email="past@example.com", message="x" * 20
        )
        old = ContactAttachment.objects.create(
            contact=contact,
            original_name="old.pdf",
            content_type="application/pdf",
            size=50 * 1024 * 1024,
            checksum="0" * 64,
            claim_deadline=timezone.now(),
        )
        ContactAttachment.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=INTAKE_WINDOW_HOURS + 1)
        )

        response = _upload(client, pdf_bytes(1024))

        assert response.status_code == status.HTTP_201_CREATED

    def test_a_store_cannot_ask_for_more_files_than_the_ceiling(
        self, private_tree, attachments_on
    ):
        """``max_count`` is capped in code, like ``max_bytes``."""
        Setting.objects.filter(name="CONTACT_ATTACHMENTS_MAX_COUNT").update(
            value_int=5000
        )

        assert (
            AttachmentPolicy().max_count == AttachmentPolicy.MAX_COUNT_CEILING
        )

    def test_an_absurd_claim_list_is_refused_by_the_field(
        self, client, private_tree, attachments_on
    ):
        """Bounded before DRF constructs a UUID for every entry."""
        response = client.post(
            CONTACT_URL,
            {
                **ENQUIRY,
                "attachmentIds": [
                    str(uuid4())
                    for _ in range(AttachmentPolicy.MAX_COUNT_CEILING + 1)
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Contact.objects.count() == 0
