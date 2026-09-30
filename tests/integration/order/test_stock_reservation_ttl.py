from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from order.stock import StockManager
from product.factories import ProductFactory


@pytest.mark.django_db
def test_reservation_expires_after_the_configured_ttl():
    """``expires_at`` is creation time plus the configured
    ``STOCK_RESERVATION_TTL_MINUTES`` — read at reservation time, so a
    tuned setting takes effect without a deploy."""
    product = ProductFactory(stock=100, num_images=0, num_reviews=0)

    with patch.object(
        StockManager, "get_reservation_ttl_minutes", return_value=7
    ):
        before = timezone.now()
        reservation = StockManager.reserve_stock(
            product_id=product.id,
            quantity=10,
            session_id="cart-ttl",
            user_id=None,
        )
        after = timezone.now()

    assert (
        before + timedelta(minutes=7)
        <= reservation.expires_at
        <= after + timedelta(minutes=7)
    )
