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

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
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


# Column types the sweep reads, as ``information_schema`` names them.
_TEXT_TYPES = ("character varying", "text", "json", "jsonb")


def _tables_holding(needles: list[str]) -> dict[str, list[str]]:
    """Each needle found anywhere in the current schema, by
    ``table.column``: every text and JSON column of every table."""
    schema = connection.schema_name
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND data_type = ANY(%s)",
            [schema, list(_TEXT_TYPES)],
        )
        columns = cursor.fetchall()
        found: dict[str, list[str]] = {}
        patterns = [f"%{needle.casefold()}%" for needle in needles]
        for table, column in columns:
            cursor.execute(
                f'SELECT lower("{column}"::text) FROM "{schema}"."{table}" '
                f'WHERE lower("{column}"::text) LIKE ANY(%s)',
                [patterns],
            )
            for (text,) in cursor.fetchall():
                for needle in needles:
                    if needle.casefold() in text:
                        found.setdefault(needle, []).append(f"{table}.{column}")
    return found


class _Couriers:
    """Both carriers' HTTP clients: every cancel is accepted."""

    def __call__(self):
        return self

    def delete_voucher(self, *_args, **_kwargs):
        return None

    def cancel_parcel(self, *_args, **_kwargs):
        return None


def test_no_value_of_the_subject_survives_on_what_erasure_keeps(
    subject, monkeypatch, django_capture_on_commit_callbacks
):
    """Nothing of the subject's survives anywhere in the store's schema:
    every text and JSON column of every table is searched after the
    erasure, so a copy under any key or in any table fails this test.

    The data gets there the way the code puts it: two orders are
    canceled through ``OrderLifecycleService.cancel_order`` with a typed reason
    (one restocks a line and refunds its payment, both cancel their
    parcel), and the carriers' records carry what each carrier writes.
    A third order is soft-deleted, as an admin delete leaves it."""
    from order.factories import OrderFactory, PaymentTransactionFactory
    from order.inventory.stock import StockManager
    from order.lifecycle.service import OrderLifecycleService
    from order.models import OrderItem
    from order.models.order import Order
    from pay_way.factories import PayWayFactory
    from product.factories import ProductFactory
    from shipping.models import ShippingProvider
    from shipping_acs.factories import (
        AcsShipmentFactory,
        AcsTrackingEventFactory,
    )
    from shipping_acs.models import AcsApiCall, AcsCodPayout, AcsShipment
    from shipping_acs.services import _audit_envelope, _delivery_notes
    from shipping_boxnow.factories import (
        BoxNowParcelEventFactory,
        BoxNowShipmentFactory,
    )
    from tests.utils.orders import courier_cash_order
    from tests.utils.stripe_refunds import FakeStripeRefunds

    monkeypatch.setattr("shipping_acs.services.AcsClient", _Couriers())
    monkeypatch.setattr("shipping_boxnow.services.BoxNowClient", _Couriers())

    name = "Ζηνοβία Αρβανιτάκη"
    phone = "6912345678"
    note = f"Κουδούνι Αρβανιτάκη, {phone}"
    ip = "203.0.113.77"
    reason = "Μετακομίζω στη Λάρισα, στο σπίτι της Ζηνοβίας"
    personal = {
        "user": subject,
        "first_name": "Ζηνοβία",
        "last_name": "Αρβανιτάκη",
        "email": "zinovia.arvanitaki@example.gr",
        "phone": f"+30{phone}",
        "street": "Ζωοδόχου Πηγής",
        "street_number": "17Β",
        "address_line_2": "Διαμ. 3Α, 2ος όροφος",
        "city": "Χαλάνδρι",
        "zipcode": "15231",
        "place": "Κάτω Χαλάνδρι",
        "customer_notes": note,
        # Billing of an order without an issued invoice: erased too.
        "billing_company_name": "Αρβανιτάκη Ζηνοβία Ατομική",
        "billing_vat_id": "998877665",
        "document_type": "RECEIPT",
        "status": "PROCESSING",
        "metadata": {
            "meta": {
                "client_ip_address": ip,
                "client_user_agent": "SubjectBrowser/1.0",
                "fbp": "fb.1.1700000000.subjectfbp",
                "fbc": "fb.1.1700000000.subjectfbc",
            },
            "promotions": [{"code": "WELCOME"}],
        },
    }

    # 1. Cash on delivery by ACS: a line restocked, the voucher deleted.
    by_acs = courier_cash_order(
        shipping_provider=ShippingProvider.objects.get(code="acs"),
        shipping_kind="home_delivery",
        payment_status="PENDING",
        **personal,
    )
    product = ProductFactory(stock=10)
    OrderItem.objects.create(
        order=by_acs,
        product=product,
        quantity=2,
        price=product.price,
        sort_order=1,
    )
    StockManager.decrement_stock(
        product_id=product.id, quantity=2, order_id=by_acs.id, reason="sold"
    )
    acs = AcsShipmentFactory(
        order=by_acs,
        with_voucher=True,
        voucher_no="7400000001",
        metadata={
            "billing_code": "BILLING",
            "multipart_vouchers": ["7400000002"],
            # A voucher a re-mint replaced: its polls still name the buyer.
            "previous_vouchers": ["7400000008"],
            "create_request": _audit_envelope(
                {"Delivery_Notes": _delivery_notes(by_acs).sent, "Weight": "1"},
                by_acs,
            ),
            "last_error": {
                "request_params": {
                    "Recipient_Name": name,
                    "Recipient_Phone": phone,
                },
            },
        },
    )
    # 2. Paid by card, to a BOX NOW locker: refunded, the parcel canceled.
    by_boxnow = OrderFactory(
        num_order_items=0,
        pay_way=PayWayFactory.create_online_payment(),
        shipping_provider=ShippingProvider.objects.get(code="boxnow"),
        shipping_kind="pickup_point",
        payment_status="COMPLETED",
        payment_id="pi_subject",
        **personal,
    )
    PaymentTransactionFactory(
        order=by_boxnow,
        amount=by_boxnow.total_price,
        provider_reference="pi_subject",
    )
    boxnow = BoxNowShipmentFactory(
        order=by_boxnow,
        with_parcel=True,
        metadata={
            "create_request": {"orderNumber": "1", "description": note},
            "last_error": {"response_text": f"bad phone {phone}"},
        },
    )
    # 3. Deleted by staff in the admin: soft, so its data is still there.
    hidden = OrderFactory(num_order_items=0, **personal)
    Order.objects.filter(pk=hidden.pk).delete()

    with (
        FakeStripeRefunds(),
        django_capture_on_commit_callbacks(execute=True),
    ):
        for order in (by_acs, by_boxnow):
            OrderLifecycleService.cancel_order(
                order,
                reason=reason,
                refund_payment=True,
                customer_initiated=False,
            )

    # What each carrier writes and sends back.
    AcsApiCall.objects.create(
        alias="ACS_Create_Voucher",
        duration_ms=1,
        outcome="ok",
        order_id=by_acs.id,
        request_params={"Recipient_Name": name, "Delivery_Notes": note},
    )
    AcsApiCall.objects.create(
        alias="ACS_Trackingsummary",
        duration_ms=1,
        outcome="ok",
        voucher_no="7400000002",
        response={"recipient": name},
    )
    # A COD answer lists many parcels and names no order or voucher.
    cod_answer = AcsApiCall.objects.create(
        alias="ACS_COD_Beneficiary_Info",
        duration_ms=1,
        outcome="ok",
        response={
            "Table_Data": [
                {"POD": "7400000001", "Parcel_Receiver": name},
                {"POD": "7499999998", "Parcel_Receiver": "ΑΛΛΟΣ ΠΕΛΑΤΗΣ"},
            ]
        },
    )
    AcsApiCall.objects.create(
        alias="ACS_Trackingsummary",
        duration_ms=1,
        outcome="ok",
        voucher_no="7400000008",
        response={"recipient": name},
    )
    other_call = AcsApiCall.objects.create(
        alias="ACS_Trackingsummary",
        duration_ms=1,
        outcome="ok",
        voucher_no="7499999999",
    )
    # What staff wrote about the money.
    AcsShipment.objects.filter(pk=acs.pk).update(
        cod_received_outside_acs_note=f"Τράπεζα, από {name}"
    )
    AcsTrackingEventFactory(
        shipment=acs,
        notes=f"Παρέλαβε η {name}",
        raw_payload={
            "checkpoint_action": "ΠΑΡΑΔΟΣΗ",
            "checkpoint_notes": f"Παρέλαβε η {name}",
        },
    )
    payout = AcsCodPayout.objects.create(
        voucher_no="7400000001",
        shipment=acs,
        pod="7400000001",
        parcel_receiver=name,
        raw_payload={"POD": "7400000001", "Parcel_Receiver": name},
        reconciliation_note=f"Πλήρωσε η {name} στο κατάστημα",
    )
    # A webhook event as BOX NOW sends it: its data names the customer.
    BoxNowParcelEventFactory(
        shipment=boxnow,
        additional_information=f"Για {name}",
        raw_payload={
            "specversion": "1.0",
            "type": "gr.boxnow.parcel_event_change",
            "data": {
                "parcelId": "9000000001",
                "event": "delivered",
                "customer": {
                    "name": name,
                    "email": "zinovia.arvanitaki@example.gr",
                    "phoneNumber": f"+30{phone}",
                },
                "additionalInformation": f"Για {name}",
            },
        },
    )

    anonymise_and_delete_user(subject)

    leaks = _tables_holding(
        [
            "Ζηνοβία",
            "Αρβανιτάκη",
            "zinovia.arvanitaki",
            phone,
            "Ζωοδόχου",
            "Διαμ. 3Α",
            "15231",
            "Κάτω Χαλάνδρι",
            ip,
            "SubjectBrowser",
            "subjectfbp",
            "subjectfbc",
            reason,
            "998877665",
        ]
    )
    assert not leaks, {k: sorted(set(v)) for k, v in leaks.items()}

    # What is not the subject's stays.
    for order in (by_acs, by_boxnow):
        order.refresh_from_db()
        assert order.personal_data_erased_at is not None
        assert order.metadata["promotions"] == [{"code": "WELCOME"}]
    assert Order.objects.all_with_deleted().get(pk=hidden.pk).user_id is None
    acs.refresh_from_db()
    assert acs.metadata["billing_code"] == "BILLING"
    assert acs.metadata["create_request"] == {"Weight": "1"}
    payout.refresh_from_db()
    # ACS's POD is the voucher number it reported, not personal data.
    assert payout.pod == "7400000001"
    assert AcsApiCall.objects.filter(pk=other_call.pk).exists()
    assert not AcsApiCall.objects.filter(pk=cod_answer.pk).exists()


def test_the_billing_of_an_issued_invoice_stays(subject):
    """The counterparty of an invoice issued in timologio is kept with it
    (tax law); the rest of the order is erased."""
    from order.factories import OrderFactory

    order = OrderFactory(
        user=subject,
        billing_vat_id="998877665",
        billing_company_name="Εταιρεία ΑΕ",
        timologio_invoice_mark=400001234567890,
    )

    anonymise_and_delete_user(subject)

    order.refresh_from_db()
    assert order.billing_vat_id == "998877665"
    assert order.billing_company_name == "Εταιρεία ΑΕ"
    assert order.first_name == "[deleted]"


@pytest.mark.parametrize(
    ("document_type", "status", "kept"),
    [
        ("INVOICE", "DELIVERED", True),
        ("INVOICE", "PENDING", True),
        ("INVOICE", "CANCELED", False),
        ("RECEIPT", "DELIVERED", False),
    ],
)
def test_the_billing_of_an_owed_invoice_stays(
    subject, document_type, status, kept
):
    """An INVOICE order staff have yet to issue in timologio needs its
    counterparty; a canceled one or a receipt does not."""
    from order.factories import OrderFactory

    order = OrderFactory(
        user=subject,
        document_type=document_type,
        status=status,
        billing_vat_id="998877665",
        billing_company_name="Αρβανιτάκη Ατομική",
    )

    anonymise_and_delete_user(subject)

    order.refresh_from_db()
    assert (order.billing_vat_id == "998877665") is kept
    assert (order.billing_company_name == "Αρβανιτάκη Ατομική") is kept


def test_a_guest_order_with_a_verified_address_is_the_subjects(subject):
    """A guest order placed with an address the account verified is the
    subject's, as every other guest record is: erased (and exported). One
    placed with an address the account never proved is not."""
    from allauth.account.models import EmailAddress
    from djmoney.money import Money

    from meta_capi.models import MetaCapiEventLog
    from order.factories import OrderFactory
    from promotion.factories.promotion import PromotionFactory
    from promotion.models.redemption import PromotionRedemption

    EmailAddress.objects.create(
        user=subject, email=subject.email, verified=True, primary=True
    )
    guest = OrderFactory(user=None, email=subject.email.upper())
    stranger = OrderFactory(user=None, email="stranger@example.gr")
    # What a guest order writes with no user: found by its order.
    redemption = PromotionRedemption.objects.create(
        promotion=PromotionFactory(),
        order=guest,
        email=subject.email,
        amount=Money("5.00", "EUR"),
    )
    MetaCapiEventLog.objects.create(
        event_name="Purchase", event_id="guest-purchase", order=guest
    )

    anonymise_and_delete_user(subject)

    guest.refresh_from_db()
    stranger.refresh_from_db()
    redemption.refresh_from_db()
    assert redemption.email == ""
    assert not MetaCapiEventLog.objects.filter(order=guest).exists()
    assert guest.email.endswith("@deleted.invalid")
    assert guest.personal_data_erased_at is not None
    assert stranger.email == "stranger@example.gr"
    assert stranger.personal_data_erased_at is None


def test_a_guest_order_under_an_unverified_address_of_the_account_stays(
    subject,
):
    """An address the account holds but never verified may be anyone's:
    a guest order placed with it is neither erased nor exported."""
    from allauth.account.models import EmailAddress

    from order.factories import OrderFactory
    from user.services.gdpr import compile_user_data

    EmailAddress.objects.create(
        user=subject, email=subject.email, verified=True, primary=True
    )
    EmailAddress.objects.create(
        user=subject, email="unproven@example.gr", verified=False
    )
    guest = OrderFactory(user=None, email="unproven@example.gr")

    assert guest.id not in {
        o["id"] for o in compile_user_data(subject)["orders"]
    }
    anonymise_and_delete_user(subject)

    guest.refresh_from_db()
    assert guest.email == "unproven@example.gr"
    assert guest.personal_data_erased_at is None


def test_carrier_data_that_arrives_later_is_erased_by_the_sweep(subject):
    """A parcel still moving when its buyer is erased goes on being
    tracked, delivered and paid out. The daily sweep clears what a carrier
    writes about an erased order, however long ago the erasure was, the
    event that settles the parcel included; each kind of write finds its
    order on its own (no shipment row was touched). An order nothing was
    written about within ``ERASURE_CARRIER_SWEEP_LOOKBACK_DAYS`` is not
    read."""
    from datetime import timedelta

    from django.utils import timezone

    from order.factories import OrderFactory
    from order.models.order import Order
    from shipping_acs.enum.shipment_state import AcsShipmentState
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
    from shipping_boxnow.factories import (
        BoxNowParcelEventFactory,
        BoxNowShipmentFactory,
    )
    from shipping_boxnow.models import BoxNowShipment
    from shipping_boxnow.models.parcel_event import BoxNowParcelEvent
    from user.tasks import erase_late_carrier_data

    def acs_shipment(voucher, **metadata):
        return AcsShipmentFactory(
            order=OrderFactory(user=subject),
            voucher_no=voucher,
            shipment_state=AcsShipmentState.IN_TRANSIT,
            metadata=metadata,
        )

    checkpoint_acs = acs_shipment("7400000003")
    acs_shipment("7400000005")
    acs_shipment("7400000011", previous_vouchers=["7400000012"])
    quiet_acs = acs_shipment("7400000004")
    boxnow_order = OrderFactory(user=subject)
    boxnow = BoxNowShipmentFactory(order=boxnow_order)
    anonymise_and_delete_user(subject)

    # Erased 400 days ago, and nothing written about them since.
    long_ago = timezone.now() - timedelta(days=400)
    Order.objects.all_with_deleted().filter(user__isnull=True).update(
        personal_data_erased_at=long_ago
    )
    AcsShipment.objects.update(updated_at=long_ago)
    BoxNowShipment.objects.update(updated_at=long_ago)

    # Today: a checkpoint, a payout ACS reported by its voucher alone, a
    # poll of a voucher a re-mint replaced, and a BOX NOW webhook.
    settling = AcsTrackingEventFactory(
        shipment=checkpoint_acs,
        notes="Παρέλαβε η ίδια",
        raw_payload={"checkpoint_notes": "Παρέλαβε η ίδια"},
    )
    payout = AcsCodPayout.objects.create(
        voucher_no="7400000005",
        parcel_receiver="Ζ. Α.",
        raw_payload={"Parcel_Receiver": "Ζ. Α."},
    )
    call = AcsApiCall.objects.create(
        alias="ACS_Trackingsummary",
        duration_ms=1,
        outcome="ok",
        voucher_no="7400000012",
        response={"recipient": "Ζ. Α."},
    )
    webhook = BoxNowParcelEventFactory(
        shipment=boxnow,
        additional_information="Στο ντουλάπι 12",
        raw_payload={"data": {"customer": {"name": "Ζ. Α."}}},
    )
    # Written long ago, and not since: not read again.
    old = AcsTrackingEventFactory(shipment=quiet_acs, notes="Κάτι παλιό")
    AcsTrackingEvent.objects.filter(pk=old.pk).update(
        created_at=long_ago, updated_at=long_ago
    )

    erase_late_carrier_data()

    settling.refresh_from_db()
    assert settling.notes == ""
    assert settling.raw_payload == {}
    payout.refresh_from_db()
    assert payout.parcel_receiver == ""
    assert payout.raw_payload == {}
    assert not AcsApiCall.objects.filter(pk=call.pk).exists()
    webhook.refresh_from_db()
    assert webhook.additional_information == ""
    assert webhook.raw_payload == {"data": {}}
    assert AcsTrackingEvent.objects.get(pk=old.pk).notes == "Κάτι παλιό"
    assert BoxNowParcelEvent.objects.count() == 1


def test_a_canceled_owed_invoice_loses_its_billing_at_the_sweep(subject):
    """An INVOICE order erased while an invoice was owed keeps its
    counterparty; once the order is canceled no invoice is owed, and the
    next sweep clears it."""
    from order.factories import OrderFactory
    from order.models.order import Order
    from user.tasks import erase_late_carrier_data

    order = OrderFactory(
        user=subject,
        document_type="INVOICE",
        status="PENDING",
        billing_vat_id="998877665",
    )
    anonymise_and_delete_user(subject)
    order.refresh_from_db()
    assert order.billing_vat_id == "998877665"

    Order.objects.filter(pk=order.pk).update(status="CANCELED")
    erase_late_carrier_data()

    order.refresh_from_db()
    assert order.billing_vat_id == ""


def test_finishing_an_earlier_erasure_stamps_only_what_it_cleared(
    subject, monkeypatch
):
    """Each batch is one transaction with the stamp last: a batch that fails
    is rolled back (its carrier records and history stay as they were, to
    be cleared with it next time) and left unstamped, and the next batch
    still runs."""
    from django.db import OperationalError

    from order import privacy
    from order.factories import OrderFactory
    from order.models.history import OrderHistory
    from shipping_acs.factories import (
        AcsShipmentFactory,
        AcsTrackingEventFactory,
    )

    def old_way(voucher):
        order = OrderFactory(
            user=None, email=f"deleted-{voucher}@deleted.invalid"
        )
        event = AcsTrackingEventFactory(
            shipment=AcsShipmentFactory(order=order, voucher_no=voucher),
            notes="Παρέλαβε η αγοράστρια",
        )
        note = OrderHistory.log_note(order=order, note="sent to a@example.gr")
        return order, event, note

    failing, failing_event, failing_note = old_way("7400000020")
    later, later_event, _ = old_way("7400000021")

    stamp = privacy._clear_and_stamp

    def stamp_or_fail(order_ids):
        if failing.id in order_ids:
            raise OperationalError("canceling statement due to timeout")
        stamp(order_ids)

    monkeypatch.setattr(privacy, "_FINISH_BATCH", 1)
    monkeypatch.setattr(privacy, "_clear_and_stamp", stamp_or_fail)

    assert privacy.finish_earlier_erasures() == (1, 1)

    failing.refresh_from_db()
    failing_event.refresh_from_db()
    failing_note.refresh_from_db()
    assert failing.personal_data_erased_at is None
    assert failing_event.notes == "Παρέλαβε η αγοράστρια"
    assert "a@example.gr" in str(failing_note.new_value)
    later.refresh_from_db()
    later_event.refresh_from_db()
    assert later.personal_data_erased_at is not None
    assert later_event.notes == ""


def test_a_namesake_keeps_their_acs_calls(subject):
    """ACS calls are found by the subject's own vouchers, never by a name
    or a phone: another customer with the same name keeps theirs."""
    from order.factories import OrderFactory
    from shipping_acs.factories import AcsShipmentFactory
    from shipping_acs.models import AcsApiCall
    from user.factories.account import UserAccountFactory

    order = OrderFactory(
        user=subject, first_name="Γιώργος", last_name="Παπαδόπουλος"
    )
    AcsShipmentFactory(order=order, voucher_no="7400000006")
    namesake = OrderFactory(
        user=UserAccountFactory(),
        first_name="Γιώργος",
        last_name="Παπαδόπουλος",
    )
    AcsShipmentFactory(order=namesake, voucher_no="7400000007")
    theirs = AcsApiCall.objects.create(
        alias="ACS_Trackingsummary",
        duration_ms=1,
        outcome="ok",
        voucher_no="7400000007",
        response={"recipient": "ΓΙΩΡΓΟΣ ΠΑΠΑΔΟΠΟΥΛΟΣ", "phone": "6912345678"},
    )
    # A voucher number inside a longer one is not that voucher.
    longer = AcsApiCall.objects.create(
        alias="ACS_COD_Beneficiary_Info",
        duration_ms=1,
        outcome="ok",
        response={"Table_Data": [{"POD": "74000000061"}]},
    )
    mine = AcsApiCall.objects.create(
        alias="ACS_COD_Beneficiary_Info",
        duration_ms=1,
        outcome="ok",
        response={"Table_Data": [{"POD": "7400000006"}]},
    )

    anonymise_and_delete_user(subject)

    assert AcsApiCall.objects.filter(pk__in=[theirs.pk, longer.pk]).count() == 2
    assert not AcsApiCall.objects.filter(pk=mine.pk).exists()


def test_an_order_whose_voucher_was_never_minted_is_erased(subject):
    """A shipment with no voucher yet (``voucher_no`` is ``None``) is part
    of the erasure like any other, and stops nothing."""
    from order.factories import OrderFactory
    from shipping_acs.factories import AcsShipmentFactory

    order = OrderFactory(user=subject, first_name="Ζηνοβία")
    shipment = AcsShipmentFactory(
        order=order,
        voucher_no=None,
        metadata={"last_error": {"request_params": {"Recipient_Phone": "1"}}},
    )

    anonymise_and_delete_user(subject)

    order.refresh_from_db()
    shipment.refresh_from_db()
    assert order.first_name == "[deleted]"
    assert "last_error" not in shipment.metadata


def test_an_order_erased_the_old_way_is_finished_by_the_next_sweep(subject):
    """An order the earlier erasure left (the placeholder email, no stamp)
    loses what it kept at the next daily sweep: the browser identity and
    the cancel reason, the reason's copies, the email addresses in its
    history, and its carrier records, in the same run. A stamped order is
    not matched again."""
    from datetime import timedelta

    from order.factories import OrderFactory
    from order.inventory.stock import StockManager
    from order.models.history import OrderHistory
    from order.models.order import Order
    from order.models.stock_log import StockLog
    from product.factories import ProductFactory
    from shipping_acs.factories import (
        AcsShipmentFactory,
        AcsTrackingEventFactory,
    )
    from user.tasks import erase_late_carrier_data

    reason = "Μετακομίζω στη Λάρισα οριστικά"
    order = OrderFactory(
        user=None,
        email="deleted-0123456789ab@deleted.invalid",
        first_name="[deleted]",
        metadata={
            "meta": {"client_ip_address": "203.0.113.9"},
            "cancellation": {"reason": reason, "previous_status": "PENDING"},
            "promotions": [{"code": "WELCOME"}],
        },
        billing_vat_id="998877665",
        document_type="RECEIPT",
    )
    note = OrderHistory.log_note(
        order=order,
        note="Order confirmation email sent to old.buyer@example.gr",
    )
    product = ProductFactory(stock=10)
    StockManager.decrement_stock(
        product_id=product.id,
        quantity=1,
        order_id=order.id,
        reason=f"Order {order.id} canceled: {reason}",
    )
    shipment = AcsShipmentFactory(order=order, voucher_no="7400000010")
    event = AcsTrackingEventFactory(
        shipment=shipment, notes="Παρέλαβε η αγοράστρια"
    )
    # Written long ago: only the finish step's own carrier erase reads them,
    # not the sweep of recent writes.
    long_ago = timezone.now() - timedelta(days=400)
    type(shipment).objects.filter(pk=shipment.pk).update(updated_at=long_ago)
    type(event).objects.filter(pk=event.pk).update(
        created_at=long_ago, updated_at=long_ago
    )
    done = OrderFactory(
        user=None,
        email="deleted-fffff@deleted.invalid",
        metadata={"kept": True, "meta": {"fbp": "fb.1.2.3"}},
    )
    Order.objects.filter(pk=done.pk).update(
        personal_data_erased_at=timezone.now()
    )

    result = erase_late_carrier_data()

    assert result["earlier_erasures_finished"] == 1
    order.refresh_from_db()
    assert "meta" not in order.metadata
    assert order.metadata["cancellation"] == {"previous_status": "PENDING"}
    assert order.metadata["promotions"] == [{"code": "WELCOME"}]
    assert order.personal_data_erased_at is not None
    assert order.billing_vat_id == ""
    note.refresh_from_db()
    assert "old.buyer@example.gr" not in str(note.new_value)
    assert not StockLog.objects.filter(
        order=order, reason__contains="Λάρισα"
    ).exists()
    event.refresh_from_db()
    assert event.notes == ""
    # An order already stamped is not matched again.
    done.refresh_from_db()
    assert done.metadata["kept"] is True
    assert done.metadata["meta"] == {"fbp": "fb.1.2.3"}
    assert erase_late_carrier_data()["earlier_erasures_finished"] == 0
