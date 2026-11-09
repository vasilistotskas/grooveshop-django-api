"""``StockManager.set_stock``: an absolute stock, one log row, no signal log."""

import pytest

from order.enum.stock_log_reason import StockLogReason
from order.exceptions import ProductNotFoundError
from order.models import StockLog
from order.stock import StockManager
from product.factories import ProductFactory
from product.models.product import Product

pytestmark = pytest.mark.django_db


def test_set_stock_writes_exactly_one_catalogue_import_log():
    product = ProductFactory(stock=10)
    StockLog.objects.filter(product=product).delete()

    changed = StockManager.set_stock(
        product.pk,
        4,
        reason_code=StockLogReason.CATALOGUE_IMPORT,
        reason="catalogue import 7",
    )

    assert changed is True
    product.refresh_from_db()
    assert product.stock == 4
    log = StockLog.objects.get(product=product)
    assert log.reason_code == StockLogReason.CATALOGUE_IMPORT
    assert log.reason == "catalogue import 7"
    assert log.operation_type == StockLog.OPERATION_DECREMENT
    assert (log.stock_before, log.quantity_delta, log.stock_after) == (
        10,
        -6,
        4,
    )


def test_set_stock_upwards_is_an_increment():
    product = ProductFactory(stock=1)
    StockLog.objects.filter(product=product).delete()

    StockManager.set_stock(
        product.pk, 9, reason_code=StockLogReason.CATALOGUE_IMPORT
    )

    log = StockLog.objects.get(product=product)
    assert log.operation_type == StockLog.OPERATION_INCREMENT
    assert log.quantity_delta == 8


def test_an_unchanged_stock_writes_nothing():
    product = ProductFactory(stock=5)
    StockLog.objects.filter(product=product).delete()
    history_before = Product.history.filter(id=product.pk).count()

    changed = StockManager.set_stock(
        product.pk, 5, reason_code=StockLogReason.CATALOGUE_IMPORT
    )

    assert changed is False
    assert not StockLog.objects.filter(product=product).exists()
    assert Product.history.filter(id=product.pk).count() == history_before


def test_a_negative_stock_is_refused():
    product = ProductFactory(stock=5)

    with pytest.raises(ValueError, match="negative"):
        StockManager.set_stock(
            product.pk, -1, reason_code=StockLogReason.CATALOGUE_IMPORT
        )

    product.refresh_from_db()
    assert product.stock == 5


def test_an_unknown_product_raises():
    with pytest.raises(ProductNotFoundError):
        StockManager.set_stock(
            10**9, 1, reason_code=StockLogReason.CATALOGUE_IMPORT
        )
