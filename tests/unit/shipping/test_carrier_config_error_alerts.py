"""A missing carrier credential must alert, not vanish.

``AcsConfigError`` and ``BoxNowConfigError`` are SIBLINGS of the
``*APIError`` classes, not subclasses. The dispatch tasks list only
``*RetryableError`` in ``autoretry_for`` and catch only ``*APIError`` in
the branch that emails an operator, so a config error matched neither:
the task died unhandled.

What that looks like in production: a tenant goes live with payment
credentials backfilled but carrier credentials still blank. Cash on
delivery is offered and accepted, the order commits, stock is
decremented, the dispatch task fires — and nothing else happens. The
customer has a confirmed PENDING order and no parcel.
``check_stale_acs_shipments`` cannot surface it either, because it keys
off shipments that already have tracking events. The code comment in
``shipping_acs/tasks.py`` cites a production order stranded for ten days
on exactly this shape.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from order.factories.order import OrderFactory
from shipping_acs.exceptions import AcsConfigError
from shipping_acs.tasks import create_acs_voucher_for_order
from shipping_boxnow.exceptions import BoxNowConfigError
from shipping_boxnow.tasks import create_boxnow_shipment_for_order


@pytest.mark.django_db
class TestCarrierConfigErrorsAlert:
    @pytest.fixture
    def order(self):
        return OrderFactory()

    def test_acs_missing_credentials_alerts_and_stops(self, order):
        with (
            patch(
                "shipping_acs.services.AcsService.create_voucher_for_order",
                side_effect=AcsConfigError("ACS_API_KEY is not set"),
            ),
            patch(
                "shipping.alerts.alert_admins_shipment_creation_failed"
            ) as alert,
        ):
            result = create_acs_voucher_for_order(order.id)

        assert result["status"] == "acs_not_configured"
        alert.assert_called_once()
        assert alert.call_args.kwargs["carrier"] == "ACS"
        assert alert.call_args.kwargs["order_id"] == order.id

    def test_boxnow_missing_credentials_alerts_and_stops(self, order):
        with (
            patch(
                "shipping_boxnow.services.BoxNowService."
                "create_shipment_for_order",
                side_effect=BoxNowConfigError("BOXNOW_CLIENT_ID is not set"),
            ),
            patch(
                "shipping.alerts.alert_admins_shipment_creation_failed"
            ) as alert,
        ):
            result = create_boxnow_shipment_for_order(order.id)

        assert result["status"] == "boxnow_not_configured"
        alert.assert_called_once()
        assert alert.call_args.kwargs["carrier"] == "BoxNow"
        assert alert.call_args.kwargs["order_id"] == order.id

    def test_config_errors_are_not_api_errors(self):
        """The relationship that made both slip through every handler."""
        from shipping_acs.exceptions import AcsAPIError
        from shipping_boxnow.exceptions import BoxNowAPIError

        assert not issubclass(AcsConfigError, AcsAPIError)
        assert not issubclass(BoxNowConfigError, BoxNowAPIError)
