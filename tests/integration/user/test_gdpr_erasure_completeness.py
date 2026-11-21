"""Right-to-erasure must actually erase, and must be possible at all.

`POST /api/v1/user/account/<pk>/delete_account` revokes the caller's
tokens synchronously, broadcasts a force-logout, and answers
"Your account is being deleted."  Everything after that happens in
`delete_user_account_task`.  Two ways that promise was broken:

1. `BlogAuthor.user` is the one `PROTECT` foreign key to `UserAccount`,
   so `user.delete()` raised `ProtectedError` for anyone who had ever
   authored a post.  The function is atomic, so the rollback left the
   account completely intact — while the person had already been logged
   out and told they were erased.  The task retries twice and gives up.

2. Every other non-cascading FK is `SET_NULL`, which severs the link and
   leaves every denormalised copy of the subject's data sitting on the
   row: search queries with their IP, user agent and session key;
   redemption and gift-card email addresses; the request metadata on
   order-history rows.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from user.services.gdpr import anonymise_and_delete_user

User = get_user_model()


@pytest.fixture
def subject(db):
    return User.objects.create_user(
        email="subject@example.gr",
        password="pw",
        username="subject",
        first_name="Real",
        last_name="Person",
    )


def test_an_account_that_authored_a_post_can_be_erased(subject):
    from blog.factories.author import BlogAuthorFactory
    from blog.factories.post import BlogPostFactory
    from blog.models.post import BlogPost

    author = BlogAuthorFactory(user=subject)
    post = BlogPostFactory(author=author)

    counts = anonymise_and_delete_user(subject)

    assert counts["blog_authors"] >= 1
    assert not User.objects.filter(pk=subject.pk).exists()

    # The article is the store's content and stays published; only the
    # authorship identity goes.
    post.refresh_from_db()
    assert BlogPost.objects.filter(pk=post.pk).exists()
    assert post.author_id is None


def test_search_history_does_not_survive_the_account(subject):
    from search.models import SearchQuery

    SearchQuery.objects.create(
        user=subject,
        query="something they would not want kept",
        ip_address="203.0.113.7",
        user_agent="Mozilla/5.0",
        session_key="sess-abc",
        results_count=3,
        estimated_total_hits=3,
    )

    anonymise_and_delete_user(subject)

    assert not SearchQuery.objects.filter(
        query="something they would not want kept"
    ).exists()


def test_promotion_and_giftcard_addresses_are_scrubbed(subject):
    from giftcard.factories import GiftCardFactory
    from giftcard.models import GiftCard

    card = GiftCardFactory(
        issued_to=subject,
        recipient_email=subject.email,
        recipient_name="Real Person",
    )

    anonymise_and_delete_user(subject)

    card.refresh_from_db()
    assert card.recipient_email == ""
    assert subject.email not in card.recipient_name
    # The instrument itself survives — erasing a holder must not destroy
    # the stored value.
    assert GiftCard.objects.filter(pk=card.pk).exists()
    assert card.code


def test_order_history_keeps_the_row_but_not_the_network_identity(subject):
    from order.factories.order import OrderFactory
    from order.models.history import OrderHistory

    order = OrderFactory(user=subject)
    entry = OrderHistory.objects.create(
        order=order,
        user=subject,
        ip_address="203.0.113.9",
        user_agent="Mozilla/5.0",
    )

    anonymise_and_delete_user(subject)

    entry.refresh_from_db()
    assert entry.ip_address is None
    assert entry.user_agent == ""


def test_the_cart_source_goes_with_the_cart_and_the_order_keeps_its_own(
    subject,
):
    """A cart's first-touch source is deleted with the cart the account
    owns. The order keeps its attribution row, checkout source and cart
    source alike, as the order itself is kept (tax law): erasure does not
    scrub either."""
    from cart.factories.cart import CartFactory
    from cart.models import CartAttribution
    from order.enum.attribution import OrderSourceType
    from order.factories import OrderAttributionFactory
    from order.factories.order import OrderFactory
    from order.models.attribution import OrderAttribution

    cart = CartFactory(user=subject, num_cart_items=0)
    CartAttribution.objects.create(
        cart=cart,
        source_type=OrderSourceType.SOCIAL,
        source="instagram",
        landing_path="/products/7",
    )
    kept = OrderAttributionFactory(
        order=OrderFactory(user=subject),
        source="google",
        landing_path="/products/7",
        cart_source_type=OrderSourceType.SOCIAL,
        cart_source="instagram",
    )

    anonymise_and_delete_user(subject)

    assert not CartAttribution.objects.exists()
    row = OrderAttribution.objects.get(pk=kept.pk)
    assert (row.source, row.landing_path) == ("google", "/products/7")
    assert row.cart_source == "instagram"


def test_a_failing_purge_is_not_reported_as_a_completed_erasure(
    subject, monkeypatch
):
    """The allauth block used to swallow its own DELETE failures.

    It sat three lines above a comment explaining why swallowing there
    is wrong — the two blocks below it had been fixed and this one had
    not — so a failure to remove the subject's email addresses still
    logged "GDPR deletion complete" and returned a tally.
    """
    from allauth.account.models import EmailAddress

    class _Boom(Exception):
        pass

    def _explode(*args, **kwargs):
        raise _Boom("delete failed")

    # On the manager INSTANCE, not its class: EmailAddress, User and
    # every other model share the same Manager class, so patching
    # there would blow up the assertion below as well.
    monkeypatch.setattr(EmailAddress.objects, "filter", _explode)

    with pytest.raises(_Boom):
        anonymise_and_delete_user(subject)

    assert User.objects.filter(pk=subject.pk).exists()


def test_export_bundles_do_not_outlive_the_account(subject, tmp_path):
    """`UserDataExport.user` is CASCADE, so the rows go — the files did not.

    A right-of-access bundle is the single most complete copy of the
    subject's data the system produces. Deleting the account took the
    row that named it and left the JSON on the private-media volume with
    nothing pointing at it; the expiry sweep walks rows, so it would
    never see the file again.
    """
    from unittest.mock import patch

    from user.models.data_export import UserDataExport

    bundle = tmp_path / "export.json"
    bundle.write_text('{"email": "subject@example.gr"}', encoding="utf-8")
    UserDataExport.objects.create(
        user=subject,
        status=UserDataExport.Status.READY,
        file_path=bundle.name,
        file_size=bundle.stat().st_size,
        expires_at=timezone.now() + timezone.timedelta(days=7),
    )

    with patch(
        "user.services.gdpr.get_export_location", return_value=str(tmp_path)
    ):
        counts = anonymise_and_delete_user(subject)

    assert counts["export_files_deleted"] == 1
    assert not bundle.exists()


def test_a_bundle_that_will_not_delete_aborts_the_whole_erasure(
    subject, tmp_path
):
    """No partial erasure: either the data is gone or nothing moved.

    Raising rather than logging means the task retries, and the retry is
    idempotent — a file already removed on the failed attempt is simply
    not there the next time.
    """
    from unittest.mock import patch

    from user.models.data_export import UserDataExport

    bundle = tmp_path / "export.json"
    bundle.write_text("{}", encoding="utf-8")
    UserDataExport.objects.create(
        user=subject,
        status=UserDataExport.Status.READY,
        file_path=bundle.name,
        file_size=2,
        expires_at=timezone.now() + timezone.timedelta(days=7),
    )

    with (
        patch(
            "user.services.gdpr.get_export_location", return_value=str(tmp_path)
        ),
        patch("os.remove", side_effect=OSError("read-only volume")),
        pytest.raises(OSError),
    ):
        anonymise_and_delete_user(subject)

    assert User.objects.filter(pk=subject.pk).exists()
    assert bundle.exists()


def test_no_value_of_the_subject_survives_on_what_erasure_keeps(subject):
    """A retained order and its carrier records keep nothing of the
    subject's: every column in ``Order.PERSONAL_FIELDS`` but the invoice's
    counterparty, ``Order.PERSONAL_METADATA``, each shipment's
    ``PERSONAL_METADATA`` (the failed request, the notes the voucher
    carried), the ACS call history, and what the carriers sent back.

    Every retained row is searched, JSON columns included, so a copy kept
    under any key fails this test, not only the keys it names."""
    from order.factories.order import OrderFactory
    from order.models.order import Order
    from shipping_acs.factories import (
        AcsShipmentFactory,
        AcsTrackingEventFactory,
    )
    from shipping_acs.models import (
        AcsApiCall,
        AcsCodPayout,
        AcsShipment,
        AcsTrackingEvent,
    )
    from shipping_acs.services import _audit_envelope, _delivery_notes
    from shipping_boxnow.factories import (
        BoxNowParcelEventFactory,
        BoxNowShipmentFactory,
    )
    from shipping_boxnow.models.parcel_event import BoxNowParcelEvent
    from shipping_boxnow.models.shipment import BoxNowShipment

    name = "Ζηνοβία Αρβανιτάκη"
    phone = "6912345678"
    note = f"Κουδούνι Αρβανιτάκη, {phone}"
    ip = "203.0.113.77"
    order = OrderFactory(
        user=subject,
        first_name="Ζηνοβία",
        last_name="Αρβανιτάκη",
        email="zinovia.arvanitaki@example.gr",
        phone=f"+30{phone}",
        street="Ζωοδόχου Πηγής",
        street_number="17Β",
        address_line_2="Διαμ. 3Α",
        city="Χαλάνδρι",
        zipcode="15231",
        place="Κάτω Χαλάνδρι",
        floor="SECOND_FLOOR",
        location_type="HOME",
        customer_notes=note,
        metadata={
            "meta": {
                "client_ip_address": ip,
                "client_user_agent": "SubjectBrowser/1.0",
                "fbp": "fb.1.1700000000.subjectfbp",
                "fbc": "fb.1.1700000000.subjectfbc",
            },
            "cancellation": {
                "reason": "Μετακομίζω στη Λάρισα",
                "previous_status": "PENDING",
            },
            "promotions": [{"code": "WELCOME"}],
        },
    )
    params = {"Delivery_Notes": _delivery_notes(order).sent, "Weight": "1"}
    acs = AcsShipmentFactory(
        order=order,
        voucher_no="7400000001",
        metadata={
            "billing_code": "BILLING",
            "multipart_vouchers": ["7400000002"],
            "create_request": _audit_envelope(params, order),
            "last_error": {
                "request_params": {
                    "Recipient_Name": name,
                    "Recipient_Phone": phone,
                },
            },
        },
    )
    AcsApiCall.objects.create(
        alias="ACS_Create_Voucher",
        duration_ms=1,
        outcome="ok",
        order_id=order.id,
        request_params={"Recipient_Name": name, "Delivery_Notes": note},
    )
    AcsApiCall.objects.create(
        alias="ACS_TrackingSummary",
        duration_ms=1,
        outcome="ok",
        voucher_no="7400000002",
        response={"recipient": name},
    )
    other_call = AcsApiCall.objects.create(
        alias="ACS_TrackingSummary",
        duration_ms=1,
        outcome="ok",
        voucher_no="7499999999",
    )
    AcsTrackingEventFactory(
        shipment=acs,
        notes=f"Παρέλαβε η {name}",
        raw_payload={"checkpoint_notes": f"Παρέλαβε η {name}"},
    )
    AcsCodPayout.objects.create(
        voucher_no="7400000001",
        shipment=acs,
        pod=name,
        parcel_receiver=name,
        raw_payload={"POD": name, "Parcel_Receiver": name},
    )
    boxnow = BoxNowShipmentFactory(
        order=order,
        metadata={
            "create_request": {"orderNumber": "1", "description": note},
            "last_error": {"response_text": f"bad phone {phone}"},
        },
    )
    BoxNowParcelEventFactory(
        shipment=boxnow,
        additional_information=f"Για {name}",
        raw_payload={"data": {"additionalInformation": f"Για {name}"}},
    )

    anonymise_and_delete_user(subject)

    retained = [
        Order.objects.filter(pk=order.pk).values().get(),
        *AcsShipment.objects.filter(order=order).values(),
        *BoxNowShipment.objects.filter(order=order).values(),
        *AcsApiCall.objects.values(),
        *AcsTrackingEvent.objects.values(),
        *AcsCodPayout.objects.values(),
        *BoxNowParcelEvent.objects.values(),
    ]
    text = json.dumps(retained, ensure_ascii=False, default=str)
    for value in (
        "Ζηνοβία",
        "Αρβανιτάκη",
        "zinovia.arvanitaki",
        phone,
        "Ζωοδόχου",
        "17Β",
        "Διαμ. 3Α",
        "Χαλάνδρι",
        "15231",
        "2ος όροφος",
        ip,
        "SubjectBrowser",
        "subjectfbp",
        "subjectfbc",
        "Λάρισα",
    ):
        assert value not in text, value

    order.refresh_from_db()
    assert order.floor == ""
    assert order.location_type == ""
    # What is not the subject's stays: the order's operational metadata,
    # the shipment's, the carrier events and an unrelated call.
    assert order.metadata["promotions"] == [{"code": "WELCOME"}]
    assert order.metadata["cancellation"] == {"previous_status": "PENDING"}
    acs.refresh_from_db()
    assert acs.metadata["billing_code"] == "BILLING"
    assert acs.metadata["create_request"] == {"Weight": "1"}
    assert AcsTrackingEvent.objects.filter(shipment=acs).exists()
    assert AcsApiCall.objects.filter(pk=other_call.pk).exists()
    assert AcsApiCall.objects.count() == 1
