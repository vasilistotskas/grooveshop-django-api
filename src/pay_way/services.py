import logging

from django.db.models import Q, QuerySet

logger = logging.getLogger(__name__)


class PayWayService:
    @staticmethod
    def is_provider_configured(provider_code: str) -> bool:
        """True when the current tenant can actually charge through the
        named provider.

        PayWay rows are per-schema, but seeding alone doesn't prove the
        tenant holds credentials — a row can be seeded before the
        merchant pastes their keys, or keys can be removed later. This
        gate keeps such providers out of the shopper-facing list AND
        blocks checkout-session creation for them.

        Which credentials a provider needs is the PROVIDER's knowledge,
        so it answers for itself via ``is_configured_for_tenant()``.
        This used to be an ``if code == "stripe" … elif "viva_wallet"``
        ladder here — a third place that had to list every vendor, and
        one more to find when adding a PSP.

        An unregistered code passes through as configured: ``""`` and
        offline processors (``cash``, ``bank_transfer``) need no
        credentials, and an online code with no adapter is caught later
        by the hosted-checkout capability gate rather than being
        silently hidden from the shopper here.
        """
        from order.payments.payment import get_payment_provider_class

        provider_class = get_payment_provider_class(provider_code)
        if provider_class is None:
            return True
        return provider_class.is_configured_for_tenant()

    @staticmethod
    def unconfigured_provider_codes(codes) -> set[str]:
        """Subset of ``codes`` the current tenant cannot charge through."""
        return {
            code
            for code in codes
            if code and not PayWayService.is_provider_configured(code)
        }

    @staticmethod
    def shopper_facing(queryset: QuerySet) -> QuerySet:
        """The pay ways of ``queryset`` a shopper may be offered.

        Active, and not through a provider this store holds no
        credentials for: a seeded-but-keyless Stripe/Viva row would
        dead-end the shopper at checkout-session creation.
        """
        queryset = queryset.filter(active=True)
        codes = set(
            queryset.exclude(provider_code="").values_list(
                "provider_code", flat=True
            )
        )
        unavailable = PayWayService.unconfigured_provider_codes(codes)
        if unavailable:
            queryset = queryset.exclude(provider_code__in=unavailable)
        return queryset

    @staticmethod
    def _exclusions(
        provider_code: str, kind: str, country_code: str | None
    ) -> QuerySet:
        """Pay-way ids switched off for (provider, kind) in a country.

        Rows with no country apply everywhere; a row with a country
        applies only when it matches ``country_code``.
        """
        from pay_way.models import PayWayShippingExclusion

        scope = Q(country__isnull=True)
        if country_code:
            scope |= Q(country_id=country_code.upper())
        return PayWayShippingExclusion.objects.filter(
            scope,
            shipping_provider__code=provider_code,
            shipping_kind=kind,
        ).values("pay_way_id")

    @staticmethod
    def filter_by_carrier(
        queryset: QuerySet,
        *,
        provider_code: str | None,
        shipping_kind: str | None,
        country_code: str | None = None,
    ) -> QuerySet:
        """Filter PayWays compatible with the chosen carrier + kind.

        Two layers of rules apply, in order:

        1. **Admin-configured exclusions** — rows in
           ``PayWayShippingExclusion`` indexed by ``(provider, kind)``
           identify pay-ways that the operator has switched off for
           that combination from the Django admin. Soft rules,
           runtime-toggleable with no redeploy.
        2. **Carrier hard constraints** — the registered adapter's
           ``filter_pay_ways(kind)`` hook applies any code-level
           vetoes for combinations the courier API genuinely rejects
           regardless of operator preference. Default base
           implementation is pass-through.

        Args:
            queryset: Base PayWay queryset.
            provider_code: ``ShippingProvider.code`` value, or None.
            shipping_kind: ``ShippingKind`` value, or None.
            country_code: ISO alpha-2 delivery country, or None. An
                exclusion row with a country applies only to that
                country; a row without one applies to every country.
                With no country known, only the every-country rows
                apply: a country-scoped rule cannot be evaluated
                without a country, so it is not guessed at.

        Returns:
            Filtered queryset. Empty/unknown inputs short-circuit to
            the input queryset unchanged (caller's choice to widen
            the search).
        """
        if not provider_code or not shipping_kind:
            return queryset

        from shipping.enum import ShippingKind
        from shipping.interfaces import get_provider, is_registered

        if not is_registered(provider_code):
            return queryset

        try:
            kind_enum = ShippingKind(shipping_kind)
        except ValueError:
            return queryset

        # Layer 1: admin-configured exclusions. Subquery so callers
        # composing paginated queries don't pay an extra round trip.
        excluded_ids = PayWayService._exclusions(
            provider_code, kind_enum.value, country_code
        )
        queryset = queryset.exclude(id__in=excluded_ids)

        # Layer 2: carrier-specific hard constraints. Default
        # implementation is pass-through; specific carriers override
        # the hook for combinations the courier API itself rejects.
        adapter = get_provider(provider_code)
        return adapter.filter_pay_ways(queryset, kind=kind_enum)

    @staticmethod
    def filter_by_shipping_kind(
        queryset: QuerySet,
        *,
        shipping_kind: str | None,
        country_code: str | None = None,
    ) -> QuerySet:
        """Filter PayWays for a kind whose carrier is not yet known.

        ``home_delivery`` is provider-agnostic in checkout: the
        storefront cannot name a carrier because Django picks the
        active home-delivery provider at order-creation time
        (``carrierForMethod`` in ``shared/shipping/index.ts`` returns
        null for it by design). Such a request therefore arrives with
        ``shippingKind`` and no ``shippingProviderCode`` — and used to
        fall straight through unfiltered, so BoxNow PAY ON THE GO, a
        locker-terminal product, was selectable on courier home
        delivery. Found on staging 2026-09-09.

        Both layers are applied as an INTERSECTION over every candidate
        carrier: a pay way survives only when no candidate excludes it
        and every candidate can settle it. A union would offer a
        settlement that one candidate cannot perform — the same class
        of bug this exists to close, just narrower.

        Candidates mirror ``ShippingService.available_options``: active
        providers with a registered adapter that advertise the kind and
        do not gate it behind their own per-kind flag. When nothing
        serves the kind there is no shipping option to pair a payment
        with, so the queryset passes through unchanged — same
        short-circuit convention as :meth:`filter_by_carrier`.

        ``country_code`` scopes the exclusions exactly as it does in
        :meth:`filter_by_carrier`.
        """
        if not shipping_kind:
            return queryset

        from shipping.enum import ShippingKind
        from shipping.interfaces import get_provider, is_registered
        from shipping.models import ShippingProvider

        try:
            kind_enum = ShippingKind(shipping_kind)
        except ValueError:
            return queryset

        support_field = (
            "supports_pickup_point"
            if kind_enum == ShippingKind.PICKUP_POINT
            else "supports_home_delivery"
        )
        candidates = []
        for provider in ShippingProvider.objects.filter(
            is_active=True, **{support_field: True}
        ):
            if not is_registered(provider.code):
                continue
            adapter = get_provider(provider.code)
            if not adapter.is_kind_enabled(
                kind_enum, country_code=country_code
            ):
                continue
            candidates.append((provider.code, adapter))

        if not candidates:
            return queryset

        for code, adapter in candidates:
            excluded_ids = PayWayService._exclusions(
                code, kind_enum.value, country_code
            )
            queryset = queryset.exclude(id__in=excluded_ids)
            queryset = adapter.filter_pay_ways(queryset, kind=kind_enum)

        return queryset

    @staticmethod
    def filter_for_shipping(
        queryset: QuerySet,
        *,
        provider_code: str | None,
        shipping_kind: str | None,
        country_code: str | None = None,
    ) -> QuerySet:
        """Filter PayWays for the delivery a shopper chose.

        ``shipping_kind`` is the required half: with a provider code that
        carrier's rules apply (:meth:`filter_by_carrier`), without one
        the rules every carrier serving the kind agrees on
        (:meth:`filter_by_shipping_kind`). A kind without a provider is
        not a caller mistake: checkout's ``home_delivery`` is
        provider-agnostic and cannot name one. Without a kind there is
        nothing to judge and the queryset passes through.
        """
        if not shipping_kind:
            return queryset
        if not provider_code:
            return PayWayService.filter_by_shipping_kind(
                queryset,
                shipping_kind=shipping_kind,
                country_code=country_code,
            )
        return PayWayService.filter_by_carrier(
            queryset,
            provider_code=provider_code,
            shipping_kind=shipping_kind,
            country_code=country_code,
        )
