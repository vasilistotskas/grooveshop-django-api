"""What the staging anonymiser does to every model, and why.

``anonymise_database`` resolves every concrete model to exactly one of:

* a ``Scrub`` — personal fields replaced in place with deterministic
  fakes; orders, products, stock and amounts stay real;
* a ``Delete`` — the rows are personal behaviour or a credential and have
  no value on staging;
* an entry in ``NOT_PERSONAL`` — the model holds nothing about a person,
  with the reason.

``tests/unit/devtools/test_anonymise_covers_every_model.py`` fails when a
model is in none of the three, when a field that looks personal is
neither scrubbed nor named in ``NOT_PERSONAL_FIELDS``, and when the GDPR
erasure (``user.services.gdpr.anonymise_and_delete_user``) handles a
model this table treats as not personal. A new model therefore cannot
reach staging un-decided — and ``scan.py`` independently refuses to
finish a run that leaves any scrubbed value behind.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from django.apps import apps
from django.db.models import Q


class Kind(StrEnum):
    """What a field holds, i.e. which fake replaces it."""

    EMAIL = "email"
    # An allauth address: the account's primary one becomes the account's
    # fake e-mail, every other one a unique fake of its own.
    ALT_EMAIL = "alt_email"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    USERNAME = "username"
    PHONE = "phone"
    STREET = "street"
    STREET_NUMBER = "street_number"
    STREET_LINE = "street_line"
    CITY = "city"
    ZIPCODE = "zipcode"
    PLACE = "place"
    COMPANY = "company"
    VAT_ID = "vat_id"
    TAX_OFFICE = "tax_office"
    ACTIVITY = "activity"
    # Text a person typed: replaced whole by a neutral sentence.
    FREE_TEXT = "free_text"
    # System text that may quote a person (an error echoing a request):
    # e-mail addresses, phone numbers and the row's own known values are
    # rewritten, the rest is kept.
    TEXT = "text"
    # A JSON document: personal keys are replaced, strings are rewritten
    # like ``TEXT``, the structure is kept.
    JSON = "json"
    BLANK = "blank"
    NULL = "null"
    IP = "ip"
    # A unique opaque string for a column that must stay unique.
    TOKEN = "token"
    PASSWORD = "password"
    FILENAME = "filename"
    # A bearer UUID (guest order, guest cart, gift-card purchase page):
    # whoever holds it can read the record on PRODUCTION, so it is
    # replaced by its deterministic staging twin everywhere it appears.
    UUID = "uuid"
    # A bearer code (gift card, personal coupon): valid on production,
    # replaced by its staging twin.
    CODE = "code"


@dataclass(frozen=True)
class F:
    """A field whose value belongs to the row, not to the linked account.

    A gift card's recipient is the account it is issued to, but its
    ``sender_name`` is someone else: ``F(Kind.FULL_NAME, own=True)``
    gives it a person of its own.
    """

    kind: Kind
    own: bool = False


@dataclass(frozen=True)
class Scrub:
    """Replace personal fields in place.

    ``user_fk`` / ``order_fk`` name the foreign key that says whose data
    the row is, so an order, its address and its invoice all resolve the
    same fake person. ``is_user`` marks the account row itself. With
    neither, the row is its own subject.
    """

    fields: Mapping[str, Kind | F]
    user_fk: str | None = None
    order_fk: str | None = None
    is_user: bool = False
    # Restrict the rule to these rows (a personal coupon's code is a
    # bearer secret; a public one is marketing and stays).
    where: Q | None = None
    # Columns set to a constant on every row, by one UPDATE that never
    # reads them back (so an encrypted token no key opens cannot stop
    # the run). Lazy: it reads the model's fields.
    clear: Callable[[], Mapping[str, object]] | None = None

    def handled_fields(self) -> set[str]:
        """Every field this rule scrubs or clears."""
        cleared = set(self.clear()) if self.clear else set()
        return set(self.fields) | cleared


@dataclass(frozen=True)
class Delete:
    """Delete every row.

    ``tenant_only`` leaves the public schema alone: its sessions belong
    to platform staff, whose logins staging keeps.
    """

    tenant_only: bool = False


def _credentials(label: str) -> Mapping[str, object]:
    """The credential columns of ``label``, derived from its fields."""
    from devtools.anonymise import credentials

    return credentials.cleared_fields(apps.get_model(label))


def _scrub_all(**fields: Kind | F) -> Scrub:
    return Scrub(fields=fields)


_ADDRESS = {
    "street": Kind.STREET,
    "street_number": Kind.STREET_NUMBER,
    "city": Kind.CITY,
    "zipcode": Kind.ZIPCODE,
}

REGISTRY: dict[str, Scrub | Delete] = {
    # ── identity ────────────────────────────────────────────────────
    "user.UserAccount": Scrub(
        is_user=True,
        fields={
            "password": Kind.PASSWORD,
            "username": Kind.USERNAME,
            "email": Kind.EMAIL,
            "first_name": Kind.FIRST_NAME,
            "last_name": Kind.LAST_NAME,
            "phone": Kind.PHONE,
            "city": Kind.CITY,
            "zipcode": Kind.ZIPCODE,
            "address": Kind.STREET_LINE,
            "place": Kind.PLACE,
            "image": Kind.NULL,
            "birth_date": Kind.NULL,
            "twitter": Kind.BLANK,
            "linkedin": Kind.BLANK,
            "facebook": Kind.BLANK,
            "instagram": Kind.BLANK,
            "website": Kind.BLANK,
            "youtube": Kind.BLANK,
            "github": Kind.BLANK,
            "bio": Kind.FREE_TEXT,
        },
    ),
    "account.EmailAddress": Scrub(
        user_fk="user", fields={"email": Kind.ALT_EMAIL}
    ),
    "user.UserAddress": Scrub(
        user_fk="user",
        fields={
            "first_name": Kind.FIRST_NAME,
            "last_name": Kind.LAST_NAME,
            "phone": Kind.PHONE,
            "notes": Kind.FREE_TEXT,
            **_ADDRESS,
        },
    ),
    "user.UserSubscription": Scrub(
        user_fk="user",
        fields={
            "email": Kind.EMAIL,
            "confirmation_token": Kind.TOKEN,
            "confirmed_ip": Kind.IP,
            "consent_ip": Kind.IP,
            "consent_user_agent": Kind.BLANK,
            "metadata": Kind.JSON,
        },
    ),
    "b2b.BusinessProfile": Scrub(
        user_fk="user",
        fields={
            "company_name": Kind.COMPANY,
            "vat_id": Kind.VAT_ID,
            "tax_office": Kind.TAX_OFFICE,
            "activity": Kind.ACTIVITY,
            "billing_street": Kind.STREET,
            "billing_street_number": Kind.STREET_NUMBER,
            "billing_city": Kind.CITY,
            "billing_zipcode": Kind.ZIPCODE,
            "vies_name": Kind.COMPANY,
            "vies_address": Kind.STREET_LINE,
            "vies_error": Kind.TEXT,
            "rejection_reason": Kind.FREE_TEXT,
        },
    ),
    "blog.BlogAuthor": Scrub(user_fk="user", fields={"website": Kind.BLANK}),
    "blog.BlogAuthorTranslation": _scrub_all(bio=Kind.FREE_TEXT),
    # ── orders ──────────────────────────────────────────────────────
    "order.Order": Scrub(
        user_fk="user",
        fields={
            "email": Kind.EMAIL,
            "first_name": Kind.FIRST_NAME,
            "last_name": Kind.LAST_NAME,
            "phone": Kind.PHONE,
            "place": Kind.PLACE,
            "customer_notes": Kind.FREE_TEXT,
            "billing_vat_id": Kind.VAT_ID,
            "billing_company_name": Kind.COMPANY,
            "billing_tax_office": Kind.TAX_OFFICE,
            "billing_activity": Kind.ACTIVITY,
            "billing_street": Kind.STREET,
            "billing_street_number": Kind.STREET_NUMBER,
            "billing_city": Kind.CITY,
            "billing_zipcode": Kind.ZIPCODE,
            "metadata": Kind.JSON,
            "private_metadata": Kind.JSON,
            "uuid": Kind.UUID,
            **_ADDRESS,
        },
    ),
    "order.OrderItem": _scrub_all(notes=Kind.FREE_TEXT),
    "order.Invoice": Scrub(
        order_fk="order",
        fields={
            "buyer_snapshot": Kind.JSON,
            "mydata_error_message": Kind.TEXT,
        },
    ),
    "order.RetailCredit": Scrub(
        order_fk="order",
        fields={
            "mydata_error_message": Kind.TEXT,
            "reason": Kind.TEXT,
        },
    ),
    "order.PaymentTransaction": Scrub(
        order_fk="order", fields={"description": Kind.TEXT}
    ),
    # The staff-typed reason of a refund: the same text as the ledger row's
    # description, and it can name the person who sent the goods back.
    "order.RefundAttempt": Scrub(
        order_fk="order", fields={"reason": Kind.TEXT}
    ),
    "order.OrderHistory": Scrub(
        order_fk="order",
        fields={
            "previous_value": Kind.JSON,
            "new_value": Kind.JSON,
            "ip_address": Kind.IP,
            "user_agent": Kind.BLANK,
        },
    ),
    "order.OrderHistoryTranslation": _scrub_all(description=Kind.TEXT),
    "order.OrderItemHistory": _scrub_all(
        previous_value=Kind.JSON, new_value=Kind.JSON
    ),
    "order.OrderItemHistoryTranslation": _scrub_all(description=Kind.TEXT),
    "order.StockLog": _scrub_all(reason=Kind.TEXT),
    # ── carriers ────────────────────────────────────────────────────
    "shipping_acs.AcsShipment": Scrub(
        order_fk="order",
        fields={
            "metadata": Kind.JSON,
            "cod_received_outside_acs_note": Kind.TEXT,
        },
    ),
    "shipping_acs.HistoricalAcsShipment": Scrub(
        order_fk="order",
        fields={
            "cod_received_outside_acs_note": Kind.TEXT,
            "history_change_reason": Kind.TEXT,
        },
    ),
    "shipping_acs.AcsTrackingEvent": _scrub_all(
        notes=Kind.TEXT, raw_payload=Kind.JSON
    ),
    "shipping_acs.AcsCodPayout": _scrub_all(
        pod=Kind.FULL_NAME,
        parcel_receiver=Kind.FULL_NAME,
        raw_payload=Kind.JSON,
        reconciliation_note=Kind.TEXT,
    ),
    "shipping_acs.AcsApiCall": _scrub_all(
        request_params=Kind.JSON,
        response=Kind.JSON,
        error_message=Kind.TEXT,
    ),
    "shipping_boxnow.BoxNowShipment": Scrub(
        order_fk="order", fields={"metadata": Kind.JSON}
    ),
    "shipping_boxnow.HistoricalBoxNowShipment": Scrub(
        order_fk="order", fields={"history_change_reason": Kind.TEXT}
    ),
    "shipping_boxnow.BoxNowParcelEvent": _scrub_all(
        additional_information=Kind.TEXT, raw_payload=Kind.JSON
    ),
    # ── enquiries and free text ─────────────────────────────────────
    "contact.Contact": _scrub_all(
        name=Kind.FULL_NAME,
        email=Kind.EMAIL,
        message=Kind.FREE_TEXT,
        company=Kind.COMPANY,
        phone=Kind.PHONE,
        subject=Kind.FREE_TEXT,
    ),
    "contact.ContactAttachment": _scrub_all(original_name=Kind.FILENAME),
    "contact.Feedback": _scrub_all(
        name=Kind.FULL_NAME,
        email=Kind.EMAIL,
        message=Kind.FREE_TEXT,
    ),
    "product.ProductReviewTranslation": _scrub_all(comment=Kind.FREE_TEXT),
    "blog.BlogCommentTranslation": _scrub_all(content=Kind.FREE_TEXT),
    "product.ProductAlert": Scrub(user_fk="user", fields={"email": Kind.EMAIL}),
    "loyalty.PointsTransaction": Scrub(
        user_fk="user", fields={"description": Kind.TEXT}
    ),
    # ── gift cards and promotions ───────────────────────────────────
    "giftcard.GiftCard": Scrub(
        user_fk="issued_to",
        fields={
            "recipient_email": Kind.EMAIL,
            "recipient_name": Kind.FULL_NAME,
            "sender_name": F(Kind.FULL_NAME, own=True),
            "message": Kind.FREE_TEXT,
            "code": Kind.CODE,
        },
    ),
    "giftcard.GiftCardPurchase": Scrub(
        user_fk="buyer",
        fields={
            "buyer_email": Kind.EMAIL,
            "sender_name": Kind.FULL_NAME,
            "recipient_email": F(Kind.EMAIL, own=True),
            "recipient_name": F(Kind.FULL_NAME, own=True),
            "message": Kind.FREE_TEXT,
            "uuid": Kind.UUID,
        },
    ),
    "giftcard.GiftCardTransaction": _scrub_all(description=Kind.TEXT),
    "promotion.PromotionCode": Scrub(
        user_fk="assigned_to",
        where=Q(assigned_to__isnull=False) | ~Q(assigned_to_email=""),
        fields={"assigned_to_email": Kind.EMAIL, "code": Kind.CODE},
    ),
    "promotion.PromotionRedemption": Scrub(
        user_fk="user", fields={"email": Kind.EMAIL}
    ),
    # ── behavioural ─────────────────────────────────────────────────
    "recommendation.RecommendationEvent": Scrub(
        user_fk="user",
        fields={"session_key": Kind.BLANK, "cart_uuid": Kind.UUID},
    ),
    "cart.Cart": Scrub(user_fk="user", fields={"uuid": Kind.UUID}),
    # ── platform control plane ──────────────────────────────────────
    # The owner's address is scrubbed; every credential column (every
    # encrypted field, the integrations' ids and keys, the live-mode
    # switches) is cleared, and staging re-arms its own sandbox ones.
    "tenant.Tenant": Scrub(
        fields={"owner_email": Kind.EMAIL},
        clear=lambda: _credentials("tenant.Tenant"),
    ),
    "tenant.HistoricalTenant": Scrub(
        fields={"owner_email": Kind.EMAIL},
        clear=lambda: _credentials("tenant.HistoricalTenant"),
    ),
    "socialaccount.SocialApp": _scrub_all(
        client_id=Kind.BLANK, secret=Kind.BLANK, key=Kind.BLANK
    ),
    "djstripe.Account": _scrub_all(stripe_data=Kind.JSON, metadata=Kind.JSON),
    # ── credentials, sessions and logs ──────────────────────────────
    "account.EmailConfirmation": Delete(),
    "knox.AuthToken": Delete(),
    "mfa.Authenticator": Delete(),
    "allauth_idp_oidc.Token": Delete(),
    "socialaccount.SocialAccount": Delete(),
    "socialaccount.SocialToken": Delete(),
    "usersessions.UserSession": Delete(tenant_only=True),
    "sessions.Session": Delete(tenant_only=True),
    "user.UserDataExport": Delete(),
    "meta_capi.MetaCapiEventLog": Delete(),
    "search.SearchQuery": Delete(),
    "notification.Notification": Delete(),
    "notification.NotificationTranslation": Delete(),
    "order.StockReservation": Delete(),
    "emails.EmailOptOut": Delete(),
    "admin.LogEntry": Delete(),
}

#: Apps whose every model is deleted except the named ones. A model added
#: to one of them is wiped by default, which is the safe direction: dj-stripe
#: mirrors Stripe objects whose JSON carries billing details, and the Celery
#: result table keeps task arguments.
WIPE_APPS: dict[str, frozenset[str]] = {
    # Account is scrubbed (REGISTRY). APIKey and WebhookEndpoint hold the
    # production Stripe secret and webhook signing secret, so they go;
    # ``manage.py bootstrap_stripe`` rebuilds both from the tenant's
    # sandbox key.
    "djstripe": frozenset({"Account"}),
    "django_celery_results": frozenset(),
}


def _exempt(reason: str, *labels: str) -> dict[str, str]:
    return dict.fromkeys(labels, reason)


#: Models that hold nothing about a person, with the reason. Grouped by
#: reason so that adding a model is a one-line decision.
NOT_PERSONAL: dict[str, str] = {
    **_exempt(
        "global reference data, identical for every store",
        "country.Country",
        "country.CountryTranslation",
        "region.Region",
        "region.RegionTranslation",
        "vat.Vat",
        "contenttypes.ContentType",
        "auth.Permission",
        "auth.Group",
        "sites.Site",
        "core.Translation",
        "tenant.TenantStatsSnapshot",
    ),
    **_exempt(
        "scheduler configuration of the platform, no customer data",
        "django_celery_beat.ClockedSchedule",
        "django_celery_beat.CrontabSchedule",
        "django_celery_beat.IntervalSchedule",
        "django_celery_beat.PeriodicTask",
        "django_celery_beat.PeriodicTasks",
        "django_celery_beat.SolarSchedule",
    ),
    **_exempt(
        "catalogue and storefront content the merchant authored",
        "product.Attribute",
        "product.AttributeTranslation",
        "product.AttributeValue",
        "product.AttributeValueTranslation",
        "product.Brand",
        "product.Product",
        "product.HistoricalProduct",
        "product.ProductAttribute",
        "product.ProductCategory",
        "product.ProductCategoryImage",
        "product.ProductCategoryImageTranslation",
        "product.ProductCategoryTranslation",
        "product.ProductImage",
        "product.ProductImageTranslation",
        "product.ProductPriceSpell",
        "product.ProductRelation",
        "product.ProductTranslation",
        "product.ProductVariantGroup",
        "product.ProductVariantGroupTranslation",
        "blog.BlogCategory",
        "blog.BlogCategoryTranslation",
        "blog.BlogPost",
        "blog.BlogPostTranslation",
        "blog.BlogTag",
        "blog.BlogTagTranslation",
        "tag.Tag",
        "tag.TagTranslation",
        "tag.TaggedItem",
        "page_config.ContentPage",
        "page_config.ContentPageTranslation",
        "page_config.LegalDocumentVersion",
        "page_config.NavigationColumn",
        "page_config.NavigationColumnTranslation",
        "page_config.NavigationLink",
        "page_config.NavigationLinkTranslation",
        "page_config.NavigationMenu",
        "page_config.PageLayout",
        "page_config.PageLayoutTranslation",
        "page_config.PageSection",
        "user.SubscriptionTopic",
        "user.SubscriptionTopicTranslation",
        "loyalty.LoyaltyTier",
        "loyalty.LoyaltyTierTranslation",
        "recommendation.RecommendationCandidate",
        "recommendation.RecommendationSlot",
    ),
    **_exempt(
        "store configuration (payment, shipping, pricing, promotion and "
        "B2B rules); carriers' public network data (stations, lockers)",
        "b2b.CustomerGroup",
        "b2b.PriceListItem",
        "pay_way.PayWay",
        "pay_way.PayWayShippingExclusion",
        "pay_way.PayWayTranslation",
        "shipping.ShippingProvider",
        "shipping.ShippingRate",
        "shipping.HistoricalShippingRate",
        "shipping_acs.AcsStation",
        "shipping_acs.AcsPickupList",
        "shipping_acs.AcsPickupListRun",
        "shipping_boxnow.BoxNowLocker",
        "promotion.Promotion",
        "promotion.PromotionTranslation",
        "promotion.CartPromotionCode",
        "loyalty.LoyaltyPause",
        "giftcard.GiftCardPause",
        "extra_settings.Setting",
        "texts.TextOverride",
        "texts.HistoricalTextOverride",
        "emails.EmailSwitch",
        "emails.HistoricalEmailSwitch",
        "allauth_idp_oidc.Client",
    ),
    **_exempt(
        "operational record of an order, keyed by an opaque id: amounts, "
        "stock, counters, provider references; the buyer's identity lives "
        "on the Order and the account, both scrubbed",
        "order.CreditCounter",
        "order.InvoiceCounter",
        "order.OrderAttribution",
        "order.OrderLegalAcceptance",
        "order.RefundNotification",
        "order.VivaWebhookEvent",
        "cart.CartAttribution",
        "cart.CartItem",
    ),
    **_exempt(
        "pseudonymous link to an account: it carries no identity of its "
        "own and the account is scrubbed",
        "product.ProductFavourite",
        "product.ProductReview",
        "blog.BlogComment",
        "notification.NotificationUser",
        "search.SearchClick",
        "tenant.UserTenantMembership",
    ),
    **_exempt(
        "platform staff identity or the platform's own record: staging "
        "keeps operator logins, and these rows are operator-made",
        "tenant.PlatformStaffToken",
        "tenant.TenantArchive",
        "tenant.TenantDomain",
        "core.CachePurgeLog",
    ),
    **_exempt(
        "a staff decision about a lost dispute: a yes/no, a timestamp and "
        "the platform staff member's id, nothing about the customer; "
        "staging keeps operator identities",
        "order.ChargebackDecision",
    ),
}

#: Individual fields that look personal (an e-mail or phone column, an IP,
#: a name-shaped attribute) and are deliberately kept, with the reason.
NOT_PERSONAL_FIELDS: dict[str, str] = {
    "core.CachePurgeLog.actor_email": (
        "the platform operator who purged a cache; staging keeps operator "
        "identities"
    ),
    "extra_settings.Setting.value_email": (
        "store configuration: the published business mailbox, not a customer"
    ),
    "tenant.Tenant.contact_email": (
        "the store's published contact mailbox, shown on its storefront"
    ),
    "tenant.Tenant.from_email": (
        "the store's sender address, tied to its SPF/DKIM domain"
    ),
    "tenant.HistoricalTenant.contact_email": (
        "the store's published contact mailbox, shown on its storefront"
    ),
    "tenant.HistoricalTenant.from_email": (
        "the store's sender address, tied to its SPF/DKIM domain"
    ),
    "shipping_acs.AcsStation.city": "a carrier branch, public network data",
    "shipping_acs.AcsStation.phone": "a carrier branch, public network data",
    "shipping_acs.AcsStation.postal_code": (
        "a carrier branch, public network data"
    ),
    "shipping_boxnow.BoxNowLocker.postal_code": (
        "a locker's location, public network data"
    ),
    "shipping_boxnow.BoxNowParcelEvent.postal_code": (
        "the locker or depot where the parcel event happened, not the recipient"
    ),
    "shipping_acs.AcsCodPayout.parcel_sender": (
        "the sender on a COD payout row is the store, not a customer"
    ),
}


def resolve(model) -> Scrub | Delete | str | None:
    """The decision for ``model``: a rule, a not-personal reason, or None."""
    label = model._meta.label
    if label in REGISTRY:
        return REGISTRY[label]
    keep = WIPE_APPS.get(model._meta.app_label)
    if keep is not None and model.__name__ not in keep:
        return Delete()
    if label in NOT_PERSONAL:
        return NOT_PERSONAL[label]
    return None


def concrete_models():
    return list(apps.get_models())
