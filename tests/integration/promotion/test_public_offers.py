"""The public offers endpoint.

Two things are load-bearing here and neither is obvious from reading the
queryset, so they get the most tests: what is EXCLUDED (personal
coupons, single-use codes, exhausted promotions), and the NULL handling
around ``usage_limit`` / ``usage_limit_total`` — the unlimited case is
the common one, and the tidier-looking SQL drops exactly those rows.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from djmoney.money import Money
from extra_settings.models import Setting
from rest_framework import status
from rest_framework.test import APIClient

from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories.promotion import (
    PromotionCodeFactory,
    PromotionFactory,
    PromotionTranslationFactory,
)
from promotion.models import PromotionRedemption


@pytest.fixture
def promotions_on():
    """Both gate tiers open.

    The plan tier reads ``get_current_tenant()``, which is None in this
    lane (``tests/conftest.py`` strips multi-tenancy) and therefore
    already passes. The runtime tier reads an extra-setting, patched at
    the classmethod rather than round-tripped through the DB — a
    ``Setting.objects.update_or_create`` → ``Setting.get`` round trip
    flakes under xdist.
    """
    original = Setting.get.__func__

    def _get(cls, key, default=None):
        if key == "PROMOTIONS_ENABLED":
            return True
        return original(cls, key, default)

    with patch.object(Setting, "get", classmethod(_get)):
        yield


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def url():
    return reverse("promotion:promotion-public-list")


def _named(promotion, name="Offer"):
    PromotionTranslationFactory(master=promotion, language_code="el", name=name)
    return promotion


def _redeem(promotion, email):
    """A redemption row. ``amount`` is a non-null MoneyField, so the
    engine always records what the promotion actually took off."""
    return PromotionRedemption.objects.create(
        promotion=promotion,
        email=email,
        amount=Money(Decimal("1.00"), "EUR"),
    )


def _automatic(**kwargs):
    kwargs.setdefault("trigger", PromotionTrigger.AUTOMATIC)
    kwargs.setdefault("is_active", True)
    return _named(PromotionFactory(**kwargs))


@pytest.mark.django_db
class TestVisibility:
    def test_lists_a_live_automatic_promotion(self, client, url, promotions_on):
        promotion = _automatic(benefit_value=Decimal("15.00"))

        response = client.get(url)

        assert response.status_code == status.HTTP_200_OK
        rows = response.json()
        assert [row["id"] for row in rows] == [promotion.id]
        assert rows[0]["code"] is None
        # COERCE_DECIMAL_TO_STRING is False project-wide, so money
        # and decimal fields land as JSON numbers.
        assert Decimal(str(rows[0]["benefitValue"])) == Decimal("15.00")

    def test_excludes_an_inactive_promotion(self, client, url, promotions_on):
        _automatic(is_active=False)

        assert client.get(url).json() == []

    def test_excludes_an_expired_promotion(self, client, url, promotions_on):
        now = timezone.now()
        _automatic(
            starts_at=now - timedelta(days=10),
            ends_at=now - timedelta(days=1),
        )

        assert client.get(url).json() == []

    def test_excludes_a_not_yet_started_promotion(
        self, client, url, promotions_on
    ):
        _automatic(starts_at=timezone.now() + timedelta(days=1))

        assert client.get(url).json() == []


@pytest.mark.django_db
class TestCodeVisibility:
    def test_publishes_an_unlimited_public_code(
        self, client, url, promotions_on
    ):
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(
            promotion=promotion, code="WELCOME10", usage_limit=None
        )

        rows = client.get(url).json()

        assert [row["code"] for row in rows] == ["WELCOME10"]

    def test_publishes_a_multi_use_code(self, client, url, promotions_on):
        """``usage_limit > 1`` is advertisable — only 1 is not."""
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(
            promotion=promotion, code="FIVEUSES", usage_limit=5
        )

        assert [row["code"] for row in client.get(url).json()] == ["FIVEUSES"]

    def test_hides_a_single_use_code(self, client, url, promotions_on):
        """Advertising a one-shot code tells everyone but the first
        shopper about an offer they cannot redeem."""
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(promotion=promotion, code="ONESHOT", usage_limit=1)

        assert client.get(url).json() == []

    def test_hides_a_code_assigned_to_a_user(
        self, client, url, promotions_on, django_user_model
    ):
        user = django_user_model.objects.create_user(
            email="someone@example.test", password="x"
        )
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(
            promotion=promotion, code="ONLYME", assigned_to=user
        )

        assert client.get(url).json() == []

    def test_hides_a_code_assigned_to_an_email(
        self, client, url, promotions_on
    ):
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(
            promotion=promotion,
            code="GUESTONLY",
            assigned_to_email="guest@example.test",
        )

        assert client.get(url).json() == []

    def test_hides_an_inactive_code(self, client, url, promotions_on):
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(
            promotion=promotion, code="RETIRED", is_active=False
        )

        assert client.get(url).json() == []

    def test_publishes_the_public_code_when_a_personal_one_also_exists(
        self, client, url, promotions_on
    ):
        """A campaign can carry both; the public one is what ships."""
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(
            promotion=promotion,
            code="PERSONAL",
            assigned_to_email="vip@example.test",
        )
        PromotionCodeFactory(promotion=promotion, code="PUBLIC10")

        assert [row["code"] for row in client.get(url).json()] == ["PUBLIC10"]


@pytest.mark.django_db
class TestUsageLimits:
    def test_lists_a_promotion_with_no_total_limit(
        self, client, url, promotions_on
    ):
        """The unlimited case — ``usage_limit_total`` is NULL — is the
        common one and must survive the limit filter."""
        promotion = _automatic(usage_limit_total=None)

        assert [row["id"] for row in client.get(url).json()] == [promotion.id]

    def test_lists_a_promotion_below_its_total_limit(
        self, client, url, promotions_on
    ):
        promotion = _automatic(usage_limit_total=3)
        _redeem(promotion, "a@example.test")

        assert [row["id"] for row in client.get(url).json()] == [promotion.id]

    def test_excludes_a_promotion_at_its_total_limit(
        self, client, url, promotions_on
    ):
        """Matches PromotionEngine's own check, so the page never
        advertises an offer the cart refuses with
        USAGE_LIMIT_REACHED."""
        promotion = _automatic(usage_limit_total=2)
        for index in range(2):
            _redeem(promotion, f"{index}@example.test")

        assert client.get(url).json() == []


@pytest.mark.django_db
class TestPayload:
    def test_never_leaks_internal_or_personal_fields(
        self, client, url, promotions_on
    ):
        promotion = _named(PromotionFactory(trigger=PromotionTrigger.CODE))
        PromotionCodeFactory(promotion=promotion, code="PUB", usage_limit=None)

        row = client.get(url).json()[0]

        for leaked in (
            "usageLimitTotal",
            "usageLimitPerCustomer",
            "assignedTo",
            "assignedToEmail",
            "priority",
            "excludedProducts",
            "excludedCategories",
            "codes",
        ):
            assert leaked not in row, leaked

    def test_exposes_the_threshold_a_shopper_needs(
        self, client, url, promotions_on
    ):
        _automatic(min_subtotal=Money(Decimal("80.00"), "EUR"))

        row = client.get(url).json()[0]

        assert Decimal(str(row["minSubtotal"])) == Decimal("80.00")

    def test_free_gift_exposes_the_actual_gift_product(
        self, client, url, promotions_on
    ):
        gift = ProductFactory(stock=5)
        promotion = _automatic(
            benefit_type=BenefitType.FREE_GIFT,
            target_scope=TargetScope.ORDER,
            get_quantity=1,
        )
        promotion.get_products.add(gift)

        row = client.get(url).json()[0]

        assert [item["id"] for item in row["rewardProducts"]] == [gift.id]
        assert "slug" in row["rewardProducts"][0]

    def test_product_scoped_promotion_reports_the_true_total(
        self, client, url, promotions_on
    ):
        """The preview list is truncated; the count must not be, or the
        page cannot decide whether to render a "see all" link."""
        from promotion.serializers import REWARD_PREVIEW_LIMIT

        promotion = _automatic(target_scope=TargetScope.PRODUCTS)
        products = [
            ProductFactory(stock=1) for _ in range(REWARD_PREVIEW_LIMIT + 3)
        ]
        promotion.products.add(*products)

        row = client.get(url).json()[0]

        assert len(row["eligibleProducts"]) == REWARD_PREVIEW_LIMIT
        assert row["eligibleProductCount"] == REWARD_PREVIEW_LIMIT + 3


@pytest.mark.django_db
class TestGates:
    def test_404_when_the_runtime_setting_is_off(self, client, url):
        """Fails CLOSED: no patch here, and PROMOTIONS_ENABLED defaults
        to False, so an unconfigured store does not leak its offers."""
        _automatic()

        assert client.get(url).status_code == status.HTTP_404_NOT_FOUND

    def test_404_when_the_plan_flag_is_off(self, client, url, promotions_on):
        _automatic()

        class TenantWithoutPromotions:
            promotions_enabled = False

        with patch(
            "tenant.membership.get_current_tenant",
            return_value=TenantWithoutPromotions(),
        ):
            response = client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestLanguage:
    """An offer answers in the language the shopper is reading.

    Every `get_name`/`get_description` used to read
    `safe_translation_getter(..., any_language=True)` with no language,
    which resolves against whatever parler has ACTIVE — the site default
    on an API request, always. So `/offers` and the product page's offer
    panel were monolingual whatever locale the URL carried: the demo
    store's English homepage carried four Greek offer cards while the
    correct English translation sat in the database, unread. Found
    2026-09-20 by reading the rendered page, not the payload.
    """

    @staticmethod
    def _translate(promotion, **by_language):
        """Set the translations the factory already created.

        `PromotionFactory` auto-creates one row per configured language
        named "Promotion <n>", and `PromotionTranslationFactory` is
        `django_get_or_create` on (language_code, master) — so calling
        it again returns the EXISTING row and silently ignores the name
        you passed. Update in place instead.
        """
        for language_code, (name, description) in by_language.items():
            translation = promotion.translations.get(
                language_code=language_code
            )
            translation.name = name
            translation.description = description
            translation.save()
        return promotion

    @classmethod
    def _bilingual(cls):
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC, is_active=True
        )
        return cls._translate(
            promotion,
            el=("Δωρεάν αποστολή", "Δωρεάν για κάθε καλάθι."),
            en=("Free shipping", "Free on every cart."),
        )

    def test_answers_in_the_requested_language(
        self, client, url, promotions_on
    ):
        self._bilingual()

        rows = client.get(url, {"languageCode": "en"}).json()

        assert rows[0]["name"] == "Free shipping"
        assert rows[0]["description"] == "Free on every cart."

    def test_answers_in_the_default_when_none_is_asked_for(
        self, client, url, promotions_on
    ):
        self._bilingual()

        rows = client.get(url).json()

        assert rows[0]["name"] == "Δωρεάν αποστολή"

    def test_translates_the_products_and_categories_it_names(
        self, client, url, promotions_on
    ):
        """The nested refs need the language too.

        `eligible_products` / `eligible_categories` / `reward_products`
        are rendered by their own serializers, which DRF does not hand
        a context unless the parent passes one. Fixing only the
        promotion's own name left `/en/offers` with English offer
        headings over Greek product and category chips.
        """
        from product.factories import ProductFactory
        from promotion.enum import TargetScope

        product = ProductFactory()
        for language_code, name in (("el", "Καλώδιο"), ("en", "Cable")):
            translation = product.translations.get(language_code=language_code)
            translation.name = name
            translation.save()

        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            is_active=True,
            target_scope=TargetScope.PRODUCTS,
        )
        self._translate(promotion, en=("Offer", ""))
        promotion.products.add(product)

        rows = client.get(url, {"languageCode": "en"}).json()

        assert rows[0]["eligibleProducts"][0]["name"] == "Cable"

    def test_falls_back_rather_than_rendering_an_empty_card(
        self, client, url, promotions_on
    ):
        # A promotion nobody has translated yet still needs a name: an
        # offer card with a blank heading is worse than one in the
        # wrong language.
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC, is_active=True
        )
        self._translate(promotion, el=("Μόνο ελληνικά", ""))
        promotion.translations.exclude(language_code="el").delete()

        rows = client.get(url, {"languageCode": "en"}).json()

        assert rows[0]["name"] == "Μόνο ελληνικά"
