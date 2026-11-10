"""Both admins' navigation, built per request.

The store admin's is ``store_sidebar``; the control plane's,
``platform_sidebar``, is at the end of the module.

``UNFOLD["SIDEBAR"]["navigation"]`` points at ``store_sidebar``: Unfold
calls it with the request on every admin page. A static list could only
hide an item behind a permission callback per entry, and it showed every
store the menus of features it does not have. Here every entry is
decided in one place:

- a model page is listed only when its ModelAdmin would open for this
  user (``has_view_or_change_permission``), so a role never sees a link
  that answers 403, and an admin restricted to superusers stays hidden;
- an optional feature follows ``admin.features``: absent from the plan,
  it is not listed; on the plan but switched off in the store settings,
  its setup pages stay with an "Off" badge and its counters go; a page
  that only receives shopper input (reviews, feedback, newsletter) goes
  with its setting (``StoreFeatures.absent_models``, which the ⌘K
  palette reads too);
- a carrier's pages are listed only while that carrier is active;
- a badge counts work that is waiting, from one cached query.

Unfold renders one level of sub-navigation: an item with ``items``
becomes a toggle. A parent left with one child is replaced by that
child, so nobody opens a menu to find a single link.

Every item carries its own ``active`` (``_mark_active``). Left to
Unfold, an entry whose page is a tab of a ``UNFOLD["TABS"]`` group lit
up whenever any tab of that group was open, so Orders, Receipts and
Credit Documents (or every Blog entry) were all highlighted at once.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from django.apps import apps
from django.conf import settings
from django.contrib import admin as django_admin
from django.urls import reverse
from django.utils.translation import gettext as _

from admin.dashboard.store.queries import attention_counts
from admin.features import Availability, store_features
from admin.permissions import opens_changelist
from core.rosetta_access import tenant_scoped_rosetta_access
from core.utils.tenant_urls import get_tenant_base_url
from tenant.membership import get_current_tenant, is_platform_superuser

Item = dict[str, Any]


def _counts(request: Any) -> dict[str, int]:
    counts = getattr(request, "_admin_attention_counts", None)
    if counts is None:
        counts = attention_counts.get()
        request._admin_attention_counts = counts
    return counts


def changelist_link(request: Any, label: str, query: str = "") -> str | None:
    """A model's store-admin changelist URL, or None if this user may
    not open it.

    The same two checks Django's own app list applies: the module (which
    is where a platform-only model is withheld on a store host) and the
    view-or-change permission. The dashboard asks the same question
    before it links anywhere.
    """
    model = apps.get_model(label)
    model_admin = django_admin.site._registry.get(model)
    if not opens_changelist(request, model_admin):
        return None
    opts = model._meta
    url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    return f"{url}?{query}" if query else url


def _page(
    request: Any, label: str, title: str, icon: str = "", **extra: Any
) -> Item | None:
    """A model's changelist entry, if this user may open it."""
    link = changelist_link(request, label)
    if link is None:
        return None
    item: Item = {"title": title, "link": link}
    if icon:
        item["icon"] = icon
    item.update(extra)
    return item


def _menu(title: str, icon: str, children: list[Item | None], **extra: Any):
    """A sub-menu; a single surviving child is listed in its place.

    Unfold drops an item without a ``link``, so the parent carries its
    first child's - the template renders a parent as a toggle anyway.
    """
    items = [child for child in children if child]
    if not items:
        return None
    if len(items) == 1:
        return {**items[0], "icon": icon, **extra}
    return {
        "title": title,
        "icon": icon,
        "link": items[0]["link"],
        "items": items,
        **extra,
    }


def _group(
    title: str | None, items: list[Item | None], *, collapsible: bool = False
) -> Item | None:
    items = [item for item in items if item]
    if not items:
        return None
    return {
        "title": title,
        "separator": title is not None,
        "collapsible": collapsible,
        "items": items,
    }


def _count_badge(count: int, variant: str = "default") -> dict[str, Any]:
    return {"badge": count or None, "badge_variant": variant}


def _off_badge() -> dict[str, Any]:
    return {"badge": _("Off"), "badge_variant": "default"}


def _availability_badge(
    availability: Availability, count: int = 0
) -> dict[str, Any]:
    """ "Off" while the feature is not live; else the count."""
    if availability.live:
        return _count_badge(count)
    return _off_badge()


def _tool(title: str, icon: str, link: str) -> Item:
    return {"title": title, "icon": icon, "link": link}


def _link_path(link: Any) -> str:
    return urlparse(str(link)).path


def _tab_stand_in(path: str, sidebar_paths: set[str]) -> str | None:
    """The sidebar entry that stands for a page with no entry of its own.

    Order Items, Order History, Stock Log and the like are only tabs of
    their ``UNFOLD["TABS"]`` group. On their pages the group's first tab
    that is in the sidebar (Orders, Blog Posts, …) is the one lit up.
    """
    for group in settings.UNFOLD.get("TABS", []):
        tab_paths = [_link_path(tab["link"]) for tab in group["items"]]
        current = next((tab for tab in tab_paths if path.startswith(tab)), None)
        if current is None or current in sidebar_paths:
            continue
        return next((tab for tab in tab_paths if tab in sidebar_paths), None)
    return None


def _mark_active(request: Any, groups: list[Item]) -> None:
    """Decide every item's ``active`` here, by one rule.

    An item is active on its own pages: the request path starts with its
    link's path (the changelist and every page below it). The dashboard
    only on the index itself. A menu is active when one of its items is.
    """
    path = request.path
    index = reverse("admin:index")
    items = [item for group in groups for item in group["items"]]
    leaves = [child for item in items for child in item.get("items", [item])]
    sidebar_paths = {_link_path(leaf["link"]) for leaf in leaves}
    stand_in = _tab_stand_in(path, sidebar_paths)

    def is_active(link: Any) -> bool:
        link_path = _link_path(link)
        if link_path == index:
            return path == index
        return path.startswith(link_path) or link_path == stand_in

    for item in items:
        children = item.get("items")
        if children:
            for child in children:
                child["active"] = is_active(child["link"])
            item["active"] = any(child["active"] for child in children)
        else:
            item["active"] = is_active(item["link"])


def _store_profile(request: Any) -> Item | None:
    """The store's own ``Tenant`` row: branding, socials, pixels and
    carrier credentials (``TENANT_SELF_EDITABLE_FIELDS``). Straight to
    its change form - the changelist would hold that one row anyway."""
    tenant = get_current_tenant()
    item = _page(request, "tenant.Tenant", _("Store Profile"), "storefront")
    if tenant is None or item is None:
        return None
    item["link"] = reverse("admin:tenant_tenant_change", args=[tenant.pk])
    return item


def store_sidebar(request: Any) -> list[Item]:
    user = request.user
    features = store_features(request)
    counts = _counts(request)

    def page(label: str, title: str, icon: str = "", **extra: Any):
        if features.is_off(label):
            extra = {**extra, **_off_badge()}
        return _page(request, label, title, icon, **extra)

    sales = _group(
        _("Sales"),
        [
            page(
                "order.Order",
                _("Orders"),
                "receipt_long",
                **_count_badge(counts["pending_orders"], "primary"),
            ),
            page("order.Invoice", _("Receipts"), "receipt"),
            page(
                "order.RetailCredit", _("Credit Documents"), "assignment_return"
            ),
            page("cart.Cart", _("Carts"), "shopping_cart"),
            _tool(_("Turnover"), "bar_chart", reverse("admin:turnover"))
            if user.has_perm("order.view_turnover_report")
            else None,
            _tool(_("Profit"), "trending_up", reverse("admin:profit"))
            if user.has_perm("order.view_profit_report")
            else None,
            page("order.Expense", _("Expenses"), "payments"),
        ],
    )

    catalog = _group(
        _("Catalog"),
        [
            page(
                "product.Product",
                _("Products"),
                "inventory_2",
                **_count_badge(counts["low_stock"], "warning"),
            ),
            page("product.ProductCategory", _("Categories"), "category"),
            page("product.Brand", _("Brands"), "verified"),
            _menu(
                _("Attributes"),
                "tune",
                [
                    page("product.Attribute", _("Attributes")),
                    page("product.AttributeValue", _("Attribute Values")),
                    page("product.ProductVariantGroup", _("Variant Groups")),
                ],
                **_availability_badge(features.state("catalogue")),
            ),
            _menu(
                _("Images"),
                "photo_library",
                [
                    page("product.ProductImage", _("Product Images")),
                    page("product.ProductCategoryImage", _("Category Images")),
                ],
                **_availability_badge(features.state("catalogue")),
            ),
            page("tag.Tag", _("Tags"), "label"),
            page(
                "product.ProductReview",
                _("Reviews"),
                "star_rate",
                **_count_badge(counts["pending_reviews"]),
            ),
        ],
    )

    customers = _group(
        _("Customers"),
        [
            page("user.UserAccount", _("Customers"), "group"),
            page("user.UserAddress", _("Addresses"), "home"),
            page(
                "contact.Contact",
                _("Messages"),
                "mail",
                **_count_badge(counts["new_contact_messages"]),
            ),
            page("contact.Feedback", _("Feedback"), "reviews"),
            page("product.ProductFavourite", _("Favourites"), "favorite"),
            _menu(
                _("Notifications"),
                "notifications",
                [
                    page("notification.Notification", _("Notifications")),
                    page("notification.NotificationUser", _("Recipients")),
                ],
            ),
            _menu(
                _("Wholesale"),
                "business_center",
                [
                    page("b2b.BusinessProfile", _("Business Customers")),
                    page("b2b.CustomerGroup", _("Customer Groups")),
                    page("b2b.PriceListItem", _("B2B Price Lists")),
                ],
                **_availability_badge(
                    features.state("wholesale"),
                    counts["pending_business_profiles"],
                ),
            ),
            _menu(
                _("Loyalty"),
                "workspace_premium",
                [
                    page("loyalty.LoyaltyTier", _("Loyalty Tiers")),
                    page("loyalty.PointsTransaction", _("Points Transactions")),
                ],
                **_availability_badge(features.state("loyalty")),
            ),
            _menu(
                _("Newsletter"),
                "campaign",
                [
                    page("user.UserSubscription", _("Subscriptions")),
                    page("user.SubscriptionTopic", _("Subscription Topics")),
                ],
            ),
        ],
    )

    marketing = _group(
        _("Marketing"),
        [
            _menu(
                _("Promotions"),
                "sell",
                [
                    page("promotion.Promotion", _("Promotions")),
                    page("promotion.PromotionCode", _("Coupon Codes")),
                    page("promotion.PromotionRedemption", _("Redemptions")),
                ],
                **_availability_badge(features.state("promotions")),
            ),
            _menu(
                _("Gift Cards"),
                "card_giftcard",
                [
                    page("giftcard.GiftCard", _("Gift Cards")),
                    page("giftcard.GiftCardPurchase", _("Purchases")),
                    page("giftcard.GiftCardTransaction", _("Transactions")),
                ],
                **_availability_badge(features.state("gift_cards")),
            ),
            _menu(
                _("Recommendations"),
                "auto_awesome",
                [
                    page("recommendation.RecommendationSlot", _("Slots")),
                    page("recommendation.RecommendationEvent", _("Events")),
                    page(
                        "recommendation.RecommendationCandidate",
                        _("Candidates"),
                    ),
                ],
                **_availability_badge(features.state("product_suggestions")),
            ),
        ],
    )

    content = _group(
        _("Content"),
        [
            page("page_config.ContentPage", _("Pages"), "description"),
            # What orders were placed under: each text exactly as accepted.
            page(
                "page_config.LegalDocumentVersion",
                _("Accepted legal texts"),
                "gavel",
            ),
            # The wording of the storefront itself (buttons, labels, notices),
            # one editor page rather than a model list.
            page("texts.TextOverride", _("Storefront Texts"), "translate"),
            # The visual editor lives on the storefront; this signs the
            # person in there with a single-use ticket.
            _tool(
                _("Edit storefront"), "edit", reverse("admin:edit-storefront")
            )
            if user.has_perm("builder.view_builderdocument")
            else None,
            # What the page builder holds, read only: the editor itself is
            # the storefront's.
            _menu(
                _("Page builder"),
                "web",
                [
                    page("builder.BuilderDocument", _("Builder documents")),
                    page("builder.BuilderRelease", _("Releases")),
                    page(
                        "builder.BuilderScheduledPublish",
                        _("Scheduled publishes"),
                    ),
                    page(
                        "builder.BuilderReference", _("Where things are used")
                    ),
                ],
            ),
            _menu(
                _("Blog"),
                "article",
                [
                    page("blog.BlogPost", _("Blog Posts")),
                    page("blog.BlogCategory", _("Blog Categories")),
                    page("blog.BlogAuthor", _("Blog Authors")),
                    page("blog.BlogComment", _("Blog Comments")),
                    page("blog.BlogTag", _("Blog Tags")),
                ],
                **_count_badge(
                    counts["pending_comments"]
                    if features.live("blog_comments")
                    else 0
                ),
            ),
        ],
    )

    inactive = features.inactive_carrier_apps()
    acs = "shipping_acs" not in inactive
    boxnow = "shipping_boxnow" not in inactive

    shipping = _group(
        _("Shipping"),
        [
            page(
                "shipping_acs.AcsShipment", _("ACS Shipments"), "local_shipping"
            )
            if acs
            else None,
            page(
                "shipping_acs.AcsUnrecognisedVoucher",
                _("ACS vouchers matching no order"),
                "qr_code",
            )
            if acs
            else None,
            page(
                "shipping_boxnow.BoxNowShipment",
                _("BoxNow Shipments"),
                "package_2",
            )
            if boxnow
            else None,
            _menu(
                _("Pickup Points"),
                "location_on",
                [
                    page("shipping_boxnow.BoxNowLocker", _("BoxNow Lockers"))
                    if boxnow
                    else None,
                    page("shipping_acs.AcsStation", _("ACS Stations"))
                    if acs
                    else None,
                ],
            ),
        ],
    )

    store_settings = _group(
        _("Settings"),
        [
            _store_profile(request),
            page("tenant.UserTenantMembership", _("Team"), "badge"),
            page("extra_settings.Setting", _("Store Settings"), "settings"),
            page("pay_way.PayWay", _("Payment Methods"), "payments"),
            page(
                "shipping.ShippingProvider",
                _("Shipping Providers"),
                "route",
            ),
            page("vat.Vat", _("VAT Rates"), "percent"),
            # Which emails the store sends, with their preview, log and
            # suppression list; one page, not a model list.
            page("emails.EmailSwitch", _("Emails"), "mail"),
            _tool(
                _("Translations"),
                "translate",
                reverse("rosetta-file-list-redirect"),
            )
            if tenant_scoped_rosetta_access(user)
            else None,
            _tool(_("Cache"), "cached", reverse("admin:clear-cache"))
            if user.has_perm("core.purge_cache")
            else None,
        ],
    )

    system = (
        _group(
            _("System"),
            [
                _menu(
                    _("Security & Access"),
                    "security",
                    [
                        page("usersessions.UserSession", _("User Sessions")),
                        page("mfa.Authenticator", _("MFA Authenticators")),
                        page(
                            "socialaccount.SocialAccount", _("Social Accounts")
                        ),
                        page("socialaccount.SocialApp", _("Social Login Apps")),
                        page("allauth_idp_oidc.Client", _("OAuth Clients")),
                        page("allauth_idp_oidc.Token", _("OAuth Tokens")),
                        page("account.EmailAddress", _("Email Addresses")),
                        page("knox.AuthToken", _("API Tokens")),
                    ],
                ),
                _menu(
                    _("Audit & Logs"),
                    "fact_check",
                    [
                        page("order.StockLog", _("Stock Logs")),
                        page("order.OrderHistory", _("Order History")),
                        page("order.OrderItemHistory", _("Order Item History")),
                        page(
                            "shipping_boxnow.BoxNowParcelEvent",
                            _("BoxNow Events"),
                        ),
                        page(
                            "shipping_acs.AcsTrackingEvent",
                            _("ACS Tracking Events"),
                        ),
                        page("shipping_acs.AcsApiCall", _("ACS API Calls")),
                        page(
                            "order.VivaWebhookEvent", _("Viva Webhook Events")
                        ),
                        page(
                            "meta_capi.MetaCapiEventLog",
                            _("Meta CAPI Event Log"),
                        ),
                        page("user.UserDataExport", _("User Data Exports")),
                    ],
                ),
                _menu(
                    _("Reconciliation"),
                    "balance",
                    [
                        page(
                            "shipping_acs.AcsPickupList", _("ACS Pickup Lists")
                        ),
                        page(
                            "shipping_acs.AcsPickupListRun",
                            _("ACS Pickup List Runs"),
                        ),
                        page("shipping_acs.AcsCodPayout", _("ACS COD Payouts")),
                        page("order.InvoiceCounter", _("Receipt Counter")),
                        page("order.CreditCounter", _("Credit Counter")),
                    ],
                ),
                _menu(
                    _("Stripe"),
                    "credit_card",
                    [
                        page("djstripe.PaymentIntent", _("Payment Intents")),
                        page("djstripe.Charge", _("Charges")),
                        page("djstripe.Refund", _("Refunds")),
                        page("djstripe.Customer", _("Stripe Customers")),
                        page("djstripe.Event", _("Stripe Events")),
                        page(
                            "djstripe.WebhookEndpoint", _("Webhook Endpoints")
                        ),
                    ],
                ),
            ],
            collapsible=True,
        )
        if is_platform_superuser(user)
        else None
    )

    dashboard = _group(
        None,
        [
            {
                "title": _("Dashboard"),
                "icon": "dashboard",
                "link": reverse("admin:index"),
            }
        ],
    )
    groups = [
        group
        for group in (
            dashboard,
            sales,
            catalog,
            customers,
            marketing,
            content,
            shipping,
            store_settings,
            system,
        )
        if group
    ]
    _mark_active(request, groups)
    return groups


def platform_sidebar(request: Any) -> list[Item]:
    """The control plane's navigation: ``UNFOLD_PLATFORM["SIDEBAR"]``.

    Every entry is listed for every platform operator, so nothing here
    reads the request. The links reverse against the request's own
    URLconf: the platform site is mounted only in
    ``PUBLIC_SCHEMA_URLCONF``.
    """

    def page(title: str, icon: str, url_name: str) -> Item:
        return _tool(title, icon, reverse(f"platform_admin:{url_name}"))

    return [
        {
            "title": _("Control plane"),
            "separator": True,
            "items": [
                page(_("Dashboard"), "hub", "index"),
                page(_("Tenants"), "storefront", "tenant_tenant_changelist"),
                page(_("Plan & Billing"), "payments", "plan_billing"),
                page(
                    _("Tenant Domains"), "dns", "tenant_tenantdomain_changelist"
                ),
                page(
                    _("Memberships"),
                    "badge",
                    "tenant_usertenantmembership_changelist",
                ),
                # What offboarding kept of a destroyed store.
                page(
                    _("Tenant Archives"),
                    "inventory",
                    "tenant_tenantarchive_changelist",
                ),
            ],
        },
        {
            "title": _("Identity"),
            "separator": True,
            "items": [
                page(
                    _("Platform Staff"),
                    "manage_accounts",
                    "user_useraccount_changelist",
                ),
                page(_("Groups"), "group", "auth_group_changelist"),
                # Platform staff's own signed-in sessions (public
                # schema); each store's are in its own admin.
                page(
                    _("User Sessions"),
                    "devices",
                    "usersessions_usersession_changelist",
                ),
            ],
        },
        {
            "title": _("Platform"),
            "separator": True,
            "items": [
                # extra_settings deliberately NOT here: every knob in
                # EXTRA_SETTINGS is STORE-scoped and read from the
                # tenant's own schema — nothing that runs in the public
                # schema reads Setting at all (verified across beat
                # tasks and the platform dashboard). Exposing the
                # section here materialized meaningless public-schema
                # rows and invited edits that silently change nothing
                # for any store.
                page(_("Countries"), "public", "country_country_changelist"),
                page(_("Regions"), "map", "region_region_changelist"),
                # One public-schema row per purge, across every store —
                # platform operations, not one merchant's.
                page(
                    _("Cache Purge Log"),
                    "cleaning_services",
                    "core_cachepurgelog_changelist",
                ),
                # One ``django_site`` row per store domain (public
                # schema, minted by ``tenant.provisioning.ensure_site``)
                # — what allauth resolves a per-store SocialApp against.
                # Platform-global, so managed only here.
                page(_("Sites"), "language", "sites_site_changelist"),
                # The platform's own mail (billing notices, staff
                # password reset, health and backup alerts): what it
                # sent and what failed, its log and suppression list.
                page(_("Emails"), "mail", "emails_emailswitch_changelist"),
                page(_("Email log"), "history", "emails_emaillog_changelist"),
                page(
                    _("Suppression list"),
                    "block",
                    "emails_emailsuppression_changelist",
                ),
            ],
        },
        # The Celery beat schedule and the task-result tables live in the
        # PUBLIC schema and drive every store's fan-out tasks — platform
        # operations, never a store's. (Task Results holds task failures
        # only: ``CELERY_TASK_IGNORE_RESULT``.)
        {
            "title": _("Background Jobs"),
            "separator": True,
            "collapsible": True,
            "items": [
                page(
                    _("Periodic Tasks"),
                    "task_alt",
                    "scheduler_periodictask_changelist",
                ),
                page(
                    _("Task Results"),
                    "checklist",
                    "django_celery_results_taskresult_changelist",
                ),
                page(
                    _("Group Results"),
                    "ballot",
                    "django_celery_results_groupresult_changelist",
                ),
            ],
        },
    ]


def storefront_url(request: Any) -> str:
    """The store's own storefront, for "View site" and the header link."""
    return get_tenant_base_url()


def ops_site_dropdown(request: Any) -> list[Item]:
    """The site-name menu of both admins: the ops consoles, for platform
    superusers only.

    Unfold renders dropdown items without a permission check, so the
    list is built per request. Every store tool has its place in the
    store sidebar's Settings group and the docs and API links sit in the
    footer; what is left are the platform's own consoles.
    """
    if not is_platform_superuser(request.user):
        return []
    return list(settings.ADMIN_OPS_LINKS)
