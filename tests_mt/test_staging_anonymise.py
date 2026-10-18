"""``staging_anonymise`` leaves no original customer value in any schema.

Runs the command over the public schema and two real tenant schemas, each
seeded with a customer whose every personal value is a recognisable
string, then reads every table of every schema back, independently of the
scanner the command uses, and requires that none of those strings
survived. Also pins what must NOT change (platform staff, the shared demo
login, orders, amounts) and that a leftover rolls the run back.

Everything runs in the test's own transaction, so it is rolled back at the
end and the shared schemas are untouched for the rest of the lane.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django_tenants.utils import (
    get_public_schema_name,
    schema_context,
    tenant_context,
)

from devtools.anonymise import engine

pytestmark = pytest.mark.django_db

PHONE = "+306955512345"
PHONE_TAIL = "6955512345"
GUEST_PHONE = "+306977712345"
GUEST_PHONE_TAIL = "6977712345"
IP = "203.0.113.77"


def _canaries(tag: str) -> dict[str, str]:
    """The originals seeded for one tenant, by what they are."""
    return {
        "email": f"aliki.{tag}@real-customers.example",
        "alt_email": f"aliki.alt.{tag}@real-customers.example",
        "guest_email": f"guest.{tag}@real-customers.example",
        "contact_email": f"contact.{tag}@real-customers.example",
        "recipient_email": f"recipient.{tag}@real-customers.example",
        "name": f"Aliki{tag} Realopoulou{tag}",
        "guest_name": f"Giorgos{tag} Guestakis{tag}",
        "street": f"Realstreet{tag}",
        "note": f"please call Aliki{tag} on the door",
    }


# Bearer values (random per run) seeded for each tenant, by tag.
BEARERS: dict[str, dict[str, str]] = {}


def _seed_customer(tenant, tag: str) -> None:
    from allauth.account.models import EmailAddress
    from knox.models import AuthToken

    from cart.factories.cart import CartFactory
    from contact.models import Contact, Feedback
    from giftcard.factories import GiftCardFactory
    from order.factories.order import OrderFactory
    from promotion.factories.promotion import (
        PromotionCodeFactory,
        PromotionFactory,
    )
    from search.models import SearchQuery
    from shipping_acs.factories import AcsShipmentFactory
    from user.factories.account import UserAccountFactory
    from user.factories.address import UserAddressFactory
    from user.models import SubscriptionTopic, UserSubscription

    c = _canaries(tag)
    first, last = c["name"].split()
    with tenant_context(tenant):
        user = UserAccountFactory(
            email=c["email"],
            first_name=first,
            last_name=last,
            phone=PHONE,
            address=c["street"],
            bio=c["note"],
            image=None,
        )
        EmailAddress.objects.create(
            user=user, email=c["email"], primary=True, verified=True
        )
        EmailAddress.objects.create(
            user=user, email=c["alt_email"], primary=False, verified=False
        )
        AuthToken.objects.create(user=user)
        UserAddressFactory(
            user=user,
            first_name=first,
            last_name=last,
            street=c["street"],
            phone=PHONE,
        )
        SearchQuery.objects.create(
            query="phone cases",
            user=user,
            ip_address=IP,
            user_agent="UA",
            results_count=0,
            estimated_total_hits=0,
        )
        topic = SubscriptionTopic.objects.create(slug=f"news-{tag}")
        UserSubscription.objects.create(
            user=None,
            email=c["guest_email"],
            topic=topic,
            consent_ip=IP,
            consent_user_agent="Mozilla",
        )
        order = OrderFactory(
            user=user,
            email=c["email"],
            first_name=first,
            last_name=last,
            street=c["street"],
            phone=PHONE,
            customer_notes=c["note"],
            num_order_items=1,
        )
        guest_first, guest_last = c["guest_name"].split()
        guest_order = OrderFactory(
            user=None,
            email=c["guest_email"],
            first_name=guest_first,
            last_name=guest_last,
            phone=GUEST_PHONE,
            num_order_items=0,
        )
        AcsShipmentFactory(
            order=guest_order,
            metadata={
                "create_request": {
                    "Recipient_Name": c["guest_name"],
                    "Recipient_Phone": GUEST_PHONE_TAIL,
                    "Recipient_Email": c["guest_email"],
                },
                "voucher": "7000000001",
            },
        )
        Contact.objects.create(
            name=c["name"],
            email=c["contact_email"],
            message=f"I am {c['name']}",
            phone=PHONE,
        )
        Feedback.objects.create(
            name=c["name"], email=c["contact_email"], rating=4, message="ok"
        )
        cart = CartFactory(is_guest=True)
        guest_order.metadata = {"cart_snapshot": {"cart_uuid": str(cart.uuid)}}
        guest_order.save(update_fields=["metadata"])
        card = GiftCardFactory(code=f"REALGIFTCODE{tag.upper()}0001")
        promotion = PromotionFactory()
        personal = PromotionCodeFactory(
            promotion=promotion,
            code=f"PERSONALCODE{tag.upper()}01",
            assigned_to=user,
        )
        public = PromotionCodeFactory(
            promotion=promotion, code=f"PUBLICCODE{tag.upper()}01"
        )
        BEARERS[tag] = {
            "order_uuid": str(order.uuid),
            "guest_order_uuid": str(guest_order.uuid),
            "cart_uuid": str(cart.uuid),
            "gift_code": card.code,
            "personal_code": personal.code,
            "public_code": public.code,
        }


def _scan_every_table(needles: list[str]) -> list[str]:
    """Independent of the command: every row of every table, as JSON."""
    hits: list[str] = []
    public = get_public_schema_name()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema = ANY(%s)",
            [[public, "mt_smoke", "mt_smoke_b"]],
        )
        tables = cursor.fetchall()
        for schema, table in tables:
            cursor.execute(
                f'SELECT to_jsonb(t)::text FROM "{schema}"."{table}" t'
            )
            body = " ".join(row[0] for row in cursor.fetchall()).lower()
            hits.extend(
                f"{schema}.{table}: {n}" for n in needles if n.lower() in body
            )
    return hits


@pytest.fixture
def staging(settings):
    settings.SYSTEM_ENV = "staging"


@pytest.fixture
def seeded(mt_tenant, mt_tenant_b):
    _seed_customer(mt_tenant, "a")
    _seed_customer(mt_tenant_b, "b")
    return mt_tenant, mt_tenant_b


def _needles() -> list[str]:
    needles = [PHONE_TAIL, GUEST_PHONE_TAIL, IP]
    for tag in ("a", "b"):
        values = _canaries(tag)
        needles.extend(values.values())
        needles.extend(values["name"].split())
        needles.extend(values["guest_name"].split())
        bearers = dict(BEARERS[tag])
        needles.extend(v for k, v in bearers.items() if k != "public_code")
    return needles


def test_no_original_value_survives_in_any_schema(seeded, staging):
    call_command("staging_anonymise")

    assert _scan_every_table(_needles()) == []


def test_orders_amounts_and_the_account_link_stay_real(seeded, staging):
    from order.models import Order
    from user.models import UserAccount

    tenant, _ = seeded
    with tenant_context(tenant):
        before = {
            o.pk: (o.amount_due, o.user_id, o.status)
            for o in Order.objects.all()
        }
        users_before = UserAccount.objects.count()

    call_command("staging_anonymise")

    with tenant_context(tenant):
        after = {
            o.pk: (o.amount_due, o.user_id, o.status)
            for o in Order.objects.all()
        }
        assert after == before
        assert UserAccount.objects.count() == users_before
        user = UserAccount.objects.get(email__endswith="@staging.invalid")
        assert user.email != _canaries("a")["email"]
        assert not user.has_usable_password()
        order = Order.objects.get(user=user)
        assert order.email == user.email
        assert order.first_name == user.first_name


def test_platform_staff_and_the_shared_demo_login_keep_working(seeded, staging):
    from extra_settings.models import Setting

    from user.factories.account import UserAccountFactory
    from user.models import UserAccount

    tenant, _ = seeded
    # Platform staff live in the public schema. Pinned explicitly: the
    # connection is left on whichever schema an earlier test used.
    with schema_context(get_public_schema_name()):
        staff = UserAccountFactory(
            email="operator@grooveshop.example", is_staff=True, image=None
        )
    password_hash = staff.password
    with tenant_context(tenant):
        for name, value in (
            ("DEMO_ACCOUNT_ENABLED", True),
            ("DEMO_ACCOUNT_EMAIL", "shopper@demo.example"),
        ):
            Setting.objects.update_or_create(
                name=name, defaults=_setting_defaults(name, value)
            )
        demo = UserAccountFactory(
            email="shopper@demo.example", first_name="Demo", image=None
        )
        demo_hash = demo.password

    call_command("staging_anonymise")

    with schema_context(get_public_schema_name()):
        staff.refresh_from_db()
    assert staff.email == "operator@grooveshop.example"
    assert staff.password == password_hash
    with tenant_context(tenant):
        demo = UserAccount.objects.get(pk=demo.pk)
        assert demo.email == "shopper@demo.example"
        assert demo.first_name == "Demo"
        assert demo.password == demo_hash


def _setting_defaults(name: str, value: object) -> dict[str, object]:
    from extra_settings.models import Setting

    if isinstance(value, bool):
        return {"value_type": Setting.TYPE_BOOL, "value_bool": value}
    return {"value_type": Setting.TYPE_EMAIL, "value_email": value}


def test_a_value_no_rule_covers_fails_the_run_and_rolls_it_back(
    seeded, staging
):
    """The scan, not the rule list, is the guarantee."""
    from extra_settings.models import Setting

    from user.models import UserAccount

    tenant, _ = seeded
    leaked = _canaries("a")["email"]
    with tenant_context(tenant):
        Setting.objects.update_or_create(
            name="UNRULED_NOTE",
            defaults={
                "value_type": Setting.TYPE_TEXT,
                "value_text": f"mail {leaked} about the order",
            },
        )

    with pytest.raises(CommandError, match="original personal values survive"):
        call_command("staging_anonymise")

    with tenant_context(tenant):
        assert UserAccount.objects.filter(email=leaked).exists()


def test_it_refuses_outside_staging(seeded, settings):
    settings.SYSTEM_ENV = "production"

    with pytest.raises(CommandError, match="refusing to anonymise"):
        call_command("staging_anonymise")

    with pytest.raises(engine.NotStaging):
        engine.anonymise_database()


def test_a_schema_with_no_tenant_row_is_refused(seeded, staging):
    with connection.cursor() as cursor:
        cursor.execute('CREATE SCHEMA "orphan_store"')

    with pytest.raises(CommandError, match="orphan_store"):
        call_command("staging_anonymise")


def test_a_second_run_changes_nothing_and_passes(seeded, staging):
    call_command("staging_anonymise")
    first = _snapshot(seeded[0])

    call_command("staging_anonymise")

    assert _snapshot(seeded[0]) == first


def _snapshot(tenant) -> tuple[list[tuple], list[tuple]]:
    from order.models import Order
    from user.models import UserAccount

    with tenant_context(tenant):
        users = list(
            UserAccount.objects.order_by("pk").values_list(
                "pk", "email", "first_name", "last_name", "phone"
            )
        )
        orders = list(
            Order.objects.order_by("pk").values_list(
                "pk", "email", "first_name", "street"
            )
        )
    return users, orders


def test_bearer_credentials_valid_on_production_are_replaced(seeded, staging):
    """A copied gift-card code, personal coupon or guest order or cart UUID
    would still work against PRODUCTION; the public coupon is marketing."""
    import uuid

    from cart.models import Cart
    from giftcard.models import GiftCard
    from order.models import Order
    from promotion.models import PromotionCode

    tenant, _ = seeded
    old = BEARERS["a"]

    call_command("staging_anonymise")

    with tenant_context(tenant):
        order = Order.objects.get(email__endswith="@staging.invalid", user=None)
        assert str(order.uuid) != old["guest_order_uuid"]
        assert order.uuid.version == 5
        new_cart = str(order.metadata["cart_snapshot"]["cart_uuid"])
        assert new_cart != old["cart_uuid"]
        assert Cart.objects.filter(uuid=uuid.UUID(new_cart)).exists()
        assert not GiftCard.objects.filter(code=old["gift_code"]).exists()
        assert GiftCard.objects.get().code.startswith("STG-")
        assert not PromotionCode.objects.filter(
            code=old["personal_code"]
        ).exists()
        assert PromotionCode.objects.filter(code=old["public_code"]).exists()


def test_every_credential_on_a_tenant_row_is_cleared_even_if_undecryptable(
    seeded, staging
):
    """Production's keys open on staging, so they are cleared; and a token
    staging's keys cannot open is cleared too, not a reason to stop."""
    from core.db.encrypted_fields import EncryptedCharField
    from tenant.models import Tenant

    tenant, _ = seeded
    encrypted = [
        f.name
        for f in Tenant._meta.concrete_fields
        if isinstance(f, EncryptedCharField)
    ]
    assert encrypted
    with schema_context(get_public_schema_name()):
        row = Tenant.objects.get(pk=tenant.pk)
        for name in encrypted:
            setattr(row, name, f"secret-{name}")
        row.viva_wallet_merchant_id = "prod-merchant"
        row.viva_wallet_live_mode = True
        row.box_now_live_mode = True
        row.stripe_publishable_key = "pk_live_prod"
        row.meta_pixel_id = "123456"
        row.save()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE tenant_tenant SET stripe_secret_key_encrypted = %s "
            "WHERE id = %s",
            ["gAAAAA-not-a-token-any-key-opens", tenant.pk],
        )

    call_command("staging_anonymise")

    with schema_context(get_public_schema_name()):
        row = Tenant.objects.get(pk=tenant.pk)
        for name in encrypted:
            assert getattr(row, name) == "", name
        assert row.viva_wallet_merchant_id == ""
        assert row.stripe_publishable_key == ""
        assert row.meta_pixel_id == ""
        assert row.viva_wallet_live_mode is False
        assert row.box_now_live_mode is False


def test_a_model_with_an_uncleared_encrypted_column_fails_the_run(
    seeded, staging, monkeypatch
):
    """The run checks every EncryptedCharField column itself, whatever the
    rules say."""
    from devtools.anonymise import rules
    from tenant.models import Tenant

    tenant, _ = seeded
    with schema_context(get_public_schema_name()):
        row = Tenant.objects.get(pk=tenant.pk)
        row.chat_api_key = "llm-key"
        row.save()
    scrub = rules.REGISTRY["tenant.Tenant"]
    monkeypatch.setitem(
        rules.REGISTRY,
        "tenant.Tenant",
        rules.Scrub(fields=scrub.fields, clear=dict),
    )

    with pytest.raises(CommandError, match="encrypted credentials survive"):
        call_command("staging_anonymise")


def test_stripe_and_oauth_app_secrets_do_not_survive(seeded, staging):
    from allauth.socialaccount.models import SocialApp

    tenant, _ = seeded
    with tenant_context(tenant):
        SocialApp.objects.create(
            provider="google",
            name="google",
            client_id="prod-client-id",
            secret="prod-oauth-secret",
        )

    call_command("staging_anonymise")

    with tenant_context(tenant):
        app = SocialApp.objects.get(provider="google")
        assert (app.client_id, app.secret, app.key) == ("", "", "")


def _stage_domain(tenant) -> None:
    from tenant.models import TenantDomain

    TenantDomain.objects.filter(tenant=tenant, is_primary=True).update(
        domain="mt-smoke-staging.test"
    )


def test_staging_arms_only_its_own_sandbox_credentials(seeded, staging):
    """Production's secret is cleared; the sealed sandbox one is written."""
    import json

    from devtools.anonymise.credentials import arm_credentials
    from tenant.models import Tenant

    tenant, _ = seeded
    _stage_domain(tenant)
    with schema_context(get_public_schema_name()):
        row = Tenant.objects.get(pk=tenant.pk)
        row.stripe_secret_key = "sk_live_production"
        row.viva_wallet_api_key = "production-viva"
        row.save()

    call_command("staging_anonymise")
    written = arm_credentials(
        json.dumps({tenant.schema_name: {"stripe_secret_key": "sk_test_own"}})
    )

    assert written == {tenant.schema_name: ["stripe_secret_key"]}
    with schema_context(get_public_schema_name()):
        row = Tenant.objects.get(pk=tenant.pk)
        assert row.stripe_secret_key == "sk_test_own"
        assert row.viva_wallet_api_key == ""


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ('{"mt_smoke": {"stripe_secrt_key": "x"}}', "not credential fields"),
        ('{"mt_smoke": {"viva_wallet_live_mode": "1"}}', "not credential"),
        ('{"mt_smoke": {"name": "x"}}', "not credential fields"),
        ('["mt_smoke"]', "must be"),
    ],
)
def test_arming_refuses_a_bad_document(seeded, staging, document, message):
    from devtools.anonymise.credentials import arm_credentials

    _stage_domain(seeded[0])

    with pytest.raises(ValueError, match=message):
        arm_credentials(document)


def test_arming_refuses_a_tenant_that_is_not_on_a_staging_host(seeded, staging):
    from devtools.anonymise.credentials import arm_credentials

    tenant, _ = seeded

    with pytest.raises(ValueError, match="not a staging host"):
        arm_credentials(
            f'{{"{tenant.schema_name}": {{"stripe_secret_key": "x"}}}}'
        )


def test_arming_refuses_a_database_that_was_not_anonymised(seeded, staging):
    from devtools.anonymise.credentials import arm_credentials
    from tenant.models import Tenant

    tenant, _ = seeded
    _stage_domain(tenant)
    with schema_context(get_public_schema_name()):
        Tenant.objects.filter(pk=tenant.pk).update(viva_wallet_live_mode=True)

    with pytest.raises(ValueError, match="did not anonymise"):
        arm_credentials(
            f'{{"{tenant.schema_name}": {{"stripe_secret_key": "x"}}}}'
        )


def test_no_credentials_supplied_means_none_written(seeded, staging):
    from devtools.anonymise.credentials import arm_credentials

    assert arm_credentials("") == {}
