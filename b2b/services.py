"""B2B wholesale program: gating, profile workflow, group pricing.

Pricing follows the ``PromotionEngine`` lesson — it is a CART-CONTEXT
computation, never a catalog mutation. Catalog caches (SWR HTML, cached
Nuxt product routes, Meilisearch ``final_price``, gateway feeds/MCP) all
stay retail; a resolved price map is bound onto the cart instance and
``CartItem`` money properties branch on it with a retail fallback.

Binding happens at the model/context level (not in serializers) because
``Cart.total_price`` feeds payment-intent verification, shipping
thresholds and payment fees — a serializer-only override would desync
what the customer is shown from what the provider charges.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from djmoney.money import Money
from extra_settings.models import Setting

from b2b.enum import BusinessProfileStatus, ViesStatus
from b2b.models import BusinessProfile, CustomerGroup, PriceListItem
from b2b.vies import ViesClient, ViesUnavailableError
from product.models.product import _quantize_cents
from tenant.celery import dispatch_on_commit

logger = logging.getLogger(__name__)

ONE_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class ResolvedPrice:
    """A group-resolved product price. ``net`` is VAT-exclusive,
    ``final`` is VAT-inclusive and capped at the retail final price."""

    net: Money
    final: Money


@dataclass
class B2BPricingContext:
    group: CustomerGroup
    prices: dict[int, ResolvedPrice]

    def price_for(self, product) -> ResolvedPrice:
        """The group price for ``product``, resolving lazily on a miss.

        The bulk map covers the items present at bind time; anything
        added AFTER the bind (an add-to-cart request prices the new
        line's ``price_at_add``; an item slipping into the order-create
        lock window) resolves here on first read and is memoized — so a
        bound cart can never silently price a line at retail.
        """
        resolved = self.prices.get(product.pk)
        if resolved is None:
            resolved = B2BPricingService.resolve_single(product, self.group)
            self.prices[product.pk] = resolved
        return resolved


class B2BService:
    @classmethod
    def is_enabled(cls) -> bool:
        """Plan AND merchant toggle — both must hold.

        The plan half is checked HERE rather than only on the b2b
        endpoints because cart resolution and order create run for
        everyone (guest checkout has no permission classes), so pricing
        would bypass an endpoint-only gate entirely. See
        tenant.membership.tenant_plan_allows.
        """
        from tenant.membership import tenant_plan_allows

        if not tenant_plan_allows("b2b_enabled"):
            return False
        return bool(Setting.get("B2B_WHOLESALE_ENABLED", default=False))

    @classmethod
    def promotions_allowed(cls) -> bool:
        """Whether promotions/coupons stack on top of wholesale prices."""
        return bool(Setting.get("B2B_ALLOW_PROMOTIONS", default=False))

    @classmethod
    def loyalty_allowed(cls) -> bool:
        """Whether wholesale orders take part in the loyalty program.

        Governs BOTH halves — earning and redeeming. The points basis
        is the retail price, so a wholesale order would otherwise earn
        retail-basis points and let them be redeemed as a further
        discount on already-negotiated prices: a retail program
        subsidizing wholesale. Off by default; one switch so the two
        halves can never disagree.
        """
        return bool(Setting.get("B2B_LOYALTY_ENABLED", default=False))

    @classmethod
    def suppresses_loyalty(cls, cart) -> bool:
        """Whether this cart's loyalty redemption must be dropped.

        True only for a cart actually bound to wholesale pricing while
        the merchant keeps wholesale out of the loyalty program.
        """
        return (
            B2BPricingService.cart_pricing_active(cart)
            and not cls.loyalty_allowed()
        )

    @classmethod
    def resolve_group(cls, user) -> CustomerGroup | None:
        """The wholesale group pricing binds to, or None.

        None unless the feature is enabled, the user is authenticated,
        their profile is APPROVED, a group is assigned and active.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        if not cls.is_enabled():
            return None
        profile = (
            BusinessProfile.objects.select_related("customer_group")
            .filter(user=user)
            .first()
        )
        if profile is None or not profile.is_approved:
            return None
        group = profile.customer_group
        if group is None or not group.is_active:
            return None
        return group

    # ── profile workflow ───────────────────────────────────────────

    # Fields whose change on an APPROVED profile forces a re-review.
    IDENTITY_FIELDS = ("company_name", "vat_id", "tax_office", "activity")

    # A VIES verdict stays fresh this long for identity-unchanged
    # resubmits (address edits, retries): every check is an outbound
    # HTTP call, so unconditional refreshing would make PUT /b2b/profile
    # a request amplifier against VIES.
    VIES_RECHECK_AFTER = timedelta(hours=24)

    @classmethod
    def submit_profile(cls, user, data: dict) -> BusinessProfile:
        """Create or update the caller's business profile.

        Re-review rules: a REJECTED profile re-enters PENDING on ANY
        resubmit (the rejection email promises re-review, and the fix
        may be address-only); an APPROVED one only when an identity
        field changed (address edits keep wholesale access); SUSPENDED
        stays SUSPENDED — leaving suspension is a merchant decision,
        not a self-service edit.

        The VIES snapshot refreshes when identity changed or the last
        verdict is stale/absent, degrading to UNAVAILABLE on outages —
        an admin decides either way. The check runs BEFORE the row lock
        below so a 5s upstream timeout never holds a transaction open.
        """
        existing = BusinessProfile.objects.filter(user=user).first()
        previous_status = existing.status if existing else None
        identity_changed = existing is None or any(
            getattr(existing, field) != data.get(field, "")
            for field in cls.IDENTITY_FIELDS
        )

        needs_vies = (
            identity_changed
            or existing.vies_status
            in (ViesStatus.UNCHECKED, ViesStatus.UNAVAILABLE)
            or existing.vies_checked_at is None
            or timezone.now() - existing.vies_checked_at
            > cls.VIES_RECHECK_AFTER
        )
        vies_snapshot = (
            cls._vies_snapshot(data["vat_id"]) if needs_vies else None
        )

        with transaction.atomic():
            # get_or_create + row lock: two concurrent first-time PUTs
            # otherwise both pass a filter().first() check and the loser
            # 500s on the OneToOne constraint.
            profile, _created = (
                BusinessProfile.objects.select_for_update().get_or_create(
                    user=user,
                    defaults={
                        field: data.get(field, "")
                        for field in cls.IDENTITY_FIELDS
                    },
                )
            )
            for field in (
                *cls.IDENTITY_FIELDS,
                "billing_street",
                "billing_street_number",
                "billing_city",
                "billing_zipcode",
            ):
                if field in data:
                    setattr(profile, field, data[field])

            if previous_status == BusinessProfileStatus.REJECTED or (
                identity_changed
                and previous_status == BusinessProfileStatus.APPROVED
            ):
                profile.status = BusinessProfileStatus.PENDING

            if vies_snapshot is not None:
                cls._apply_vies(profile, vies_snapshot)
            profile.save()

        if (
            profile.status == BusinessProfileStatus.PENDING
            and previous_status != BusinessProfileStatus.PENDING
        ):
            cls._queue_admin_application_email(profile)
        return profile

    @classmethod
    def _vies_snapshot(cls, vat_id: str) -> dict:
        """Run the VIES check and return the snapshot field values."""
        snapshot: dict = {"vies_checked_at": timezone.now()}
        try:
            result = ViesClient().check_vat("EL", vat_id)
        except ViesUnavailableError as exc:
            snapshot.update(
                vies_status=ViesStatus.UNAVAILABLE,
                vies_error=str(exc)[:255],
                vies_name="",
                vies_address="",
            )
            logger.warning(
                "VIES unavailable while checking ΑΦΜ %s: %s", vat_id, exc
            )
            return snapshot
        snapshot.update(
            vies_status=(
                ViesStatus.VALID if result.valid else ViesStatus.INVALID
            ),
            vies_name=result.name[:255],
            vies_address=result.address[:255],
            vies_error="",
        )
        return snapshot

    @staticmethod
    def _apply_vies(profile: BusinessProfile, snapshot: dict) -> None:
        for field, value in snapshot.items():
            setattr(profile, field, value)

    @classmethod
    def recheck_vies(cls, profile: BusinessProfile) -> BusinessProfile:
        cls._apply_vies(profile, cls._vies_snapshot(profile.vat_id))
        profile.save(
            update_fields=[
                "vies_status",
                "vies_checked_at",
                "vies_name",
                "vies_address",
                "vies_error",
                "updated_at",
            ]
        )
        return profile

    # Review transitions re-fetch under a row lock and save ONLY the
    # workflow columns: the reviewer's in-memory row may be stale (the
    # customer can resubmit between page load and the click), and a
    # full-row save would silently clobber that resubmission.
    _REVIEW_FIELDS = (
        "status",
        "customer_group",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "updated_at",
    )

    @classmethod
    def approve(
        cls, profile: BusinessProfile, *, group: CustomerGroup, reviewed_by
    ) -> BusinessProfile:
        with transaction.atomic():
            profile = BusinessProfile.objects.select_for_update().get(
                pk=profile.pk
            )
            profile.status = BusinessProfileStatus.APPROVED
            profile.customer_group = group
            profile.reviewed_by = reviewed_by
            profile.reviewed_at = timezone.now()
            profile.rejection_reason = ""
            profile.save(update_fields=cls._REVIEW_FIELDS)
            cls._queue_status_email(profile)
        return profile

    @classmethod
    def reject(
        cls, profile: BusinessProfile, *, reason: str, reviewed_by
    ) -> BusinessProfile:
        with transaction.atomic():
            profile = BusinessProfile.objects.select_for_update().get(
                pk=profile.pk
            )
            profile.status = BusinessProfileStatus.REJECTED
            profile.rejection_reason = reason
            profile.reviewed_by = reviewed_by
            profile.reviewed_at = timezone.now()
            profile.save(update_fields=cls._REVIEW_FIELDS)
            cls._queue_status_email(profile)
        return profile

    @classmethod
    def suspend(
        cls, profile: BusinessProfile, *, reviewed_by
    ) -> BusinessProfile:
        """Silently cut wholesale access — no email, deliberately."""
        with transaction.atomic():
            profile = BusinessProfile.objects.select_for_update().get(
                pk=profile.pk
            )
            profile.status = BusinessProfileStatus.SUSPENDED
            profile.reviewed_by = reviewed_by
            profile.reviewed_at = timezone.now()
            profile.save(update_fields=cls._REVIEW_FIELDS)
        return profile

    @classmethod
    def _queue_status_email(cls, profile: BusinessProfile) -> None:
        from b2b.tasks import (
            send_business_profile_status_email,
        )

        profile_id = profile.pk
        dispatch_on_commit(send_business_profile_status_email, [profile_id])

    @classmethod
    def import_price_lines(cls, group: CustomerGroup, text: str) -> dict:
        """Bulk-upsert price-list rows from pasted ``sku;net_price``
        lines.

        Hundreds of SKUs per tier are untenable one admin form at a
        time — this backs the Customer Group admin's "Import prices"
        dialog. Separator is ``;`` or ``,``; the decimal comma is
        accepted (Greek convention). Returns a summary dict:
        ``{"created": n, "updated": n, "errors": [str, …]}``.
        """
        from product.models.product import Product

        created = 0
        updated = 0
        errors: list[str] = []
        for line_no, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            if ";" in line:
                parts = [part.strip() for part in line.split(";")]
            else:
                parts = [part.strip() for part in line.split(",", 1)]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                errors.append(
                    f"line {line_no}: expected 'sku;net_price', got {line!r}"
                )
                continue
            sku, price_raw = parts
            try:
                net = Decimal(price_raw.replace(",", "."))
            except InvalidOperation:
                errors.append(f"line {line_no}: invalid price {price_raw!r}")
                continue
            if net < 0:
                errors.append(f"line {line_no}: negative price {price_raw!r}")
                continue
            product = Product.objects.filter(sku=sku).first()
            if product is None:
                errors.append(f"line {line_no}: unknown SKU {sku!r}")
                continue
            _item, was_created = PriceListItem.objects.update_or_create(
                group=group,
                product=product,
                defaults={"net_price": Money(net, settings.DEFAULT_CURRENCY)},
            )
            if was_created:
                created += 1
            else:
                updated += 1
        return {"created": created, "updated": updated, "errors": errors}

    @classmethod
    def _queue_admin_application_email(cls, profile: BusinessProfile) -> None:
        from b2b.tasks import (
            send_admin_new_business_profile_email,
        )

        profile_id = profile.pk
        dispatch_on_commit(send_admin_new_business_profile_email, [profile_id])


class B2BPricingService:
    """Resolves and binds group prices. All rounding is 2dp
    ROUND_HALF_UP via ``_quantize_cents`` — the AADE convention every
    other money path in the codebase uses."""

    @classmethod
    def resolve(
        cls,
        product,
        group: CustomerGroup,
        *,
        override_net: Decimal | None = None,
    ) -> ResolvedPrice:
        """Group price for one product.

        Rules: a fixed price-list override wins over the group percent;
        the retail ``discount_percent`` is NEVER stacked on top; the
        result is floored at the retail final price so a retail sale
        can't be undercut by its own wholesale tier.
        """
        currency = settings.DEFAULT_CURRENCY
        if override_net is not None:
            net_amount = _quantize_cents(Decimal(override_net))
        else:
            net_amount = _quantize_cents(
                product.price.amount
                * (ONE_HUNDRED - group.discount_percent)
                / ONE_HUNDRED
            )
        rate = product.vat_percent or Decimal(0)
        vat_factor = (ONE_HUNDRED + rate) / ONE_HUNDRED
        final_amount = _quantize_cents(net_amount * vat_factor)

        retail_final = product.final_price.amount
        if final_amount >= retail_final:
            final_amount = retail_final
            net_amount = _quantize_cents(final_amount / vat_factor)

        return ResolvedPrice(
            net=Money(net_amount, currency),
            final=Money(final_amount, currency),
        )

    @classmethod
    def resolve_single(cls, product, group: CustomerGroup) -> ResolvedPrice:
        """Resolve ONE product (its own override lookup) — the lazy
        path behind ``B2BPricingContext.price_for``."""
        item = PriceListItem.objects.filter(
            group=group, product=product
        ).first()
        return cls.resolve(
            product,
            group,
            override_net=item.net_price.amount if item else None,
        )

    @classmethod
    def resolve_map(
        cls, products, group: CustomerGroup
    ) -> dict[int, ResolvedPrice]:
        products = list(products)
        overrides = {
            item.product_id: item.net_price.amount
            for item in PriceListItem.objects.filter(
                group=group, product_id__in=[p.pk for p in products]
            )
        }
        return {
            product.pk: cls.resolve(
                product, group, override_net=overrides.get(product.pk)
            )
            for product in products
        }

    @classmethod
    def bind_cart(cls, cart, user) -> B2BPricingContext | None:
        """Attach the resolved price map to the cart instance.

        No-op (and unbinds nothing) for guests, non-approved users, or
        when the feature is off — every consumer falls back to retail
        through the ``getattr`` read in ``CartItem``.
        """
        if cart is None:
            return None
        group = B2BService.resolve_group(user)
        if group is None:
            return None
        products = [
            item.product for item in cart.items.select_related("product__vat")
        ]
        context = B2BPricingContext(
            group=group, prices=cls.resolve_map(products, group)
        )
        cart._b2b_pricing = context
        return context

    @classmethod
    def cart_pricing_active(cls, cart) -> bool:
        return getattr(cart, "_b2b_pricing", None) is not None

    @classmethod
    def min_order_value_unmet(cls, cart) -> Money | None:
        """The group's minimum order value when the BOUND cart is below
        it, else None.

        One authority for all three consumers — the cart payload's
        ``below_minimum`` flag, ``create_payment_intent`` (refusing to
        mint an intent that order-create would reject after capture),
        and both order-create paths.
        """
        context = getattr(cart, "_b2b_pricing", None)
        if context is None:
            return None
        minimum = context.group.min_order_value
        if minimum.amount > 0 and cart.total_price.amount < minimum.amount:
            return minimum
        return None
