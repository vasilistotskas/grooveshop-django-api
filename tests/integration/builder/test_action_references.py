"""What a button's action names is a reference like any other: indexed, checked
at publish, and followed when the record changes."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from builder import services
from builder.data_purge import _promotion_documents
from builder.errors import DocumentInvalid
from builder.models import BuilderReference, ReferenceKind
from product.factories.product import ProductFactory
from promotion.factories.promotion import PromotionCodeFactory, PromotionFactory
from promotion.models.promotion import PromotionTrigger
from tests.utils.builder import document, section

pytestmark = pytest.mark.django_db

HOME = "template:home"


@pytest.fixture
def actions(catalogue):
    """The hero block also takes a button's action, declared as the storefront
    declares it."""
    hero = next(b for b in catalogue.blocks if b["type"] == "hero")
    hero["schema"]["properties"]["ctaAction"] = {
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "to": {},
            "product": {"type": "integer"},
            "promotion": {"type": "integer"},
        },
    }
    hero["linkRefs"] = ["/ctaAction/to"]
    hero["data"] = {
        "/ctaAction/product": {
            "field": "ref",
            "kind": "product",
            "onMissing": "empty",
        },
        "/ctaAction/promotion": {
            "field": "ref",
            "kind": "promotion",
            "onMissing": "empty",
        },
    }
    catalogue.data_limits = {
        "maxPickedIds": 24,
        "maxFilterIds": 5,
        "maxQueriesPerDocument": 12,
        "maxQueryLimit": 24,
    }
    return catalogue


def publish_with(user, action):
    services.save_draft(
        user,
        key=HOME,
        draft=document(
            ("a", section("hero@2", {"title": "Welcome", "ctaAction": action}))
        ),
        i18n={},
        expected_revision=0,
    )
    services.publish(user, keys=[HOME])


def offer(**fields):
    promotion = PromotionFactory(**fields)
    PromotionCodeFactory(promotion=promotion)
    return promotion


def refused_kinds(user, action) -> set[str]:
    with pytest.raises(DocumentInvalid) as caught:
        publish_with(user, action)
    assert {i.code for i in caught.value.issues} == {"reference_not_found"}
    return {i.params["kind"] for i in caught.value.issues}


class TestIndexed:
    def test_the_product_a_cart_button_adds(self, admin, actions, features_on):
        product = ProductFactory(active=True)

        publish_with(admin, {"kind": "addToCart", "product": product.pk})

        row = BuilderReference.objects.get()
        assert (row.kind, row.target) == (
            ReferenceKind.PRODUCT,
            str(product.pk),
        )

    def test_the_offer_a_code_button_copies(self, admin, actions, features_on):
        promotion = offer()

        publish_with(admin, {"kind": "copyCode", "promotion": promotion.pk})

        row = BuilderReference.objects.get()
        assert (row.kind, row.target) == (
            ReferenceKind.PROMOTION,
            str(promotion.pk),
        )

    def test_the_record_a_link_goes_to(self, admin, actions, features_on):
        product = ProductFactory(active=True)

        publish_with(
            admin,
            {
                "kind": "link",
                "to": {"ref": {"kind": "product", "id": product.pk}},
            },
        )

        row = BuilderReference.objects.get()
        assert (row.kind, row.target) == (
            ReferenceKind.PRODUCT,
            str(product.pk),
        )


class TestPublishRefuses:
    def test_a_product_that_is_gone(self, admin, actions, features_on):
        action = {"kind": "addToWishlist", "product": 987654}

        assert refused_kinds(admin, action) == {"product"}

    def test_an_offer_that_does_not_exist(self, admin, actions, features_on):
        action = {"kind": "copyCode", "promotion": 987654}

        assert refused_kinds(admin, action) == {"promotion"}

    def test_an_offer_that_ended(self, admin, actions, features_on):
        ended = offer(ends_at=timezone.now() - timedelta(days=1))

        assert refused_kinds(
            admin, {"kind": "copyCode", "promotion": ended.pk}
        ) == {"promotion"}

    def test_an_offer_switched_off(self, admin, actions, features_on):
        off = offer(is_active=False)

        assert refused_kinds(
            admin, {"kind": "copyCode", "promotion": off.pk}
        ) == {"promotion"}

    def test_an_automatic_offer_which_has_no_code_to_copy(
        self, admin, actions, features_on
    ):
        automatic = PromotionFactory(trigger=PromotionTrigger.AUTOMATIC)

        assert refused_kinds(
            admin, {"kind": "copyCode", "promotion": automatic.pk}
        ) == {"promotion"}

    def test_a_personal_code_never_reaches_a_public_page(
        self, admin, actions, features_on
    ):
        personal = PromotionFactory()
        PromotionCodeFactory(promotion=personal, usage_limit=1)

        assert refused_kinds(
            admin, {"kind": "copyCode", "promotion": personal.pk}
        ) == {"promotion"}


class TestPurge:
    def test_a_change_to_an_offer_reaches_the_page_with_its_button(
        self, tenant
    ):
        BuilderReference.objects.create(
            kind=ReferenceKind.PROMOTION,
            target="1",
            document="template:landing",
            node_path="/x",
        )

        assert "template:landing" in _promotion_documents()
