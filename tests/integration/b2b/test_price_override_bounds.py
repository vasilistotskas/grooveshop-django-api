"""A wholesale override must be a price, in a currency the resolver honours.

`PriceListItem.net_price` carried no validators, while its sibling
`CustomerGroup.discount_percent` in the same app has had
`MinValueValidator(0)` all along and `import_price_lines` refuses a
negative on the pasted-text path. The admin form was the way in.

`resolve` only clamps a final price from ABOVE (at the retail price, so
a wholesale tier cannot undercut a retail sale). Nothing clamps it from
below, so a negative override passes straight through into the line
total: adding the product to a B2B order makes the order cheaper.

The resolver also works entirely in `DEFAULT_CURRENCY` — it reads
`product.price.amount` and stamps that currency on the result — while
`settings.CURRENCY_CHOICES` offered USD in the admin's dropdown. A `$50`
override became `€50`, the same silent 1:1 conversion fixed for gift
cards.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from djmoney.money import Money

from b2b.factories import CustomerGroupFactory
from b2b.models.price import PriceListItem
from b2b.services import B2BPricingService
from product.factories.product import ProductFactory

pytestmark = pytest.mark.django_db


def test_a_negative_override_is_refused_by_the_model():
    item = PriceListItem(
        group=CustomerGroupFactory(),
        product=ProductFactory(),
        net_price=Money("-10.00", settings.DEFAULT_CURRENCY),
    )

    with pytest.raises(ValidationError):
        item.full_clean()


def test_the_currency_field_offers_only_what_the_resolver_can_honour():
    choices = PriceListItem._meta.get_field("net_price_currency").choices

    assert [code for code, _label in choices] == [settings.DEFAULT_CURRENCY], (
        "The resolver stamps DEFAULT_CURRENCY on every result, so any "
        "other choice here is a 1:1 conversion waiting to happen."
    )


def test_a_valid_override_still_wins_over_the_group_discount():
    group = CustomerGroupFactory(discount_percent=Decimal(10))
    product = ProductFactory(price=Money("100.00", settings.DEFAULT_CURRENCY))
    PriceListItem.objects.create(
        group=group,
        product=product,
        net_price=Money("42.00", settings.DEFAULT_CURRENCY),
    )

    resolved = B2BPricingService.resolve_single(product, group)

    # 42 is well under any retail price the factory can produce for a
    # 100.00 product, so the resolver's retail clamp cannot alter it.
    assert resolved.net.amount == Decimal("42.00")


def test_a_foreign_currency_row_is_ignored_not_converted(caplog):
    """Rows written before the field was restricted must not mis-price."""
    group = CustomerGroupFactory(discount_percent=Decimal(10))
    product = ProductFactory(price=Money("100.00", settings.DEFAULT_CURRENCY))
    item = PriceListItem.objects.create(
        group=group,
        product=product,
        net_price=Money("42.00", settings.DEFAULT_CURRENCY),
    )
    # Straight to the column: the point is a row the current field
    # definition would no longer accept.
    PriceListItem.objects.filter(pk=item.pk).update(net_price_currency="USD")

    # What the resolver would say with no override row at all. Asserting
    # against this rather than a literal keeps the test honest about
    # ProductFactory's randomised VAT rate and retail discount, both of
    # which feed the resolver's retail-price clamp.
    without_override = B2BPricingService.resolve(
        product, group, override_net=None
    )

    with caplog.at_level("ERROR"):
        resolved = B2BPricingService.resolve_single(product, group)

    assert resolved.net.amount != Decimal("42.00"), (
        "a dollar figure was charged as euros"
    )
    assert resolved.net == without_override.net, (
        "an unusable row must resolve exactly as no row would"
    )
    assert "cannot convert" in caplog.text


def test_the_bulk_map_path_ignores_the_same_row(caplog):
    group = CustomerGroupFactory(discount_percent=Decimal(10))
    product = ProductFactory(price=Money("100.00", settings.DEFAULT_CURRENCY))
    item = PriceListItem.objects.create(
        group=group,
        product=product,
        net_price=Money("42.00", settings.DEFAULT_CURRENCY),
    )
    PriceListItem.objects.filter(pk=item.pk).update(net_price_currency="USD")
    without_override = B2BPricingService.resolve(
        product, group, override_net=None
    )

    with caplog.at_level("ERROR"):
        resolved = B2BPricingService.resolve_map([product], group)

    assert resolved[product.pk].net == without_override.net


def test_a_negative_row_written_past_the_validator_is_ignored(caplog):
    """`MinMoneyValidator` is a form-layer refusal, not a database one.

    `objects.create` and `update_or_create` bypass validators entirely,
    so the model change closes the admin form and nothing else. The
    resolver is the one point every read passes through.
    """
    group = CustomerGroupFactory(discount_percent=Decimal(10))
    product = ProductFactory(price=Money("100.00", settings.DEFAULT_CURRENCY))
    PriceListItem.objects.create(
        group=group,
        product=product,
        net_price=Money("-10.00", settings.DEFAULT_CURRENCY),
    )
    without_override = B2BPricingService.resolve(
        product, group, override_net=None
    )

    with caplog.at_level("ERROR"):
        resolved = B2BPricingService.resolve_single(product, group)

    assert resolved.net.amount > 0
    assert resolved.net == without_override.net
    assert "is negative" in caplog.text
