import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from django.conf import settings
from django.utils import timezone
from djmoney.money import Money

from country.models import Country
from region.models import Region

logger = logging.getLogger(__name__)


class ShippingMethodType(Enum):
    STANDARD = "STANDARD"
    EXPRESS = "EXPRESS"
    NEXT_DAY = "NEXT_DAY"
    ECONOMY = "ECONOMY"
    INTERNATIONAL = "INTERNATIONAL"
    PICKUP = "PICKUP"


@dataclass
class ShippingOption:
    id: str
    name: str
    method_type: ShippingMethodType
    price: Money
    estimated_delivery_min: int
    estimated_delivery_max: int
    carrier: str
    carrier_service_code: str
    description: str | None = None

    @property
    def estimated_delivery_date_min(self) -> date:
        return timezone.now().date() + timedelta(
            days=self.estimated_delivery_min
        )

    @property
    def estimated_delivery_date_max(self) -> date:
        return timezone.now().date() + timedelta(
            days=self.estimated_delivery_max
        )


class ShippingCarrier(ABC):
    @abstractmethod
    def get_shipping_options(
        self,
        weight: Decimal,
        dimensions: dict[str, Decimal],
        from_country: Country,
        to_country: Country,
        to_region: Region | None = None,
        to_postal_code: str | None = None,
        **kwargs,
    ) -> list[ShippingOption]:
        pass

    @abstractmethod
    def create_shipment(
        self,
        order_id: str,
        shipping_option_id: str,
        address_data: dict[str, Any],
        **kwargs,
    ) -> tuple[bool, dict[str, Any]]:
        pass

    @abstractmethod
    def get_tracking_info(self, tracking_number: str) -> dict[str, Any]:
        pass


class FedExCarrier(ShippingCarrier):
    def __init__(self):
        self.api_key = settings.FEDEX_API_KEY
        self.account_number = settings.FEDEX_ACCOUNT_NUMBER

        # @TODO - In a real implementation, we would import and use FedEx SDK

    def get_shipping_options(
        self,
        weight: Decimal,
        dimensions: dict[str, Decimal],
        from_country: Country,
        to_country: Country,
        to_region: Region | None = None,
        to_postal_code: str | None = None,
        **kwargs,
    ) -> list[ShippingOption]:
        logger.info(
            "Getting FedEx shipping options",
            extra={
                "weight": str(weight),
                "from_country": from_country.alpha_2,
                "to_country": to_country.alpha_2,
                "to_postal_code": to_postal_code,
            },
        )

        # @TODO - Mock implementation - in real world, we would call FedEx API
        # and parse the response

        options = []

        # For domestic shipments, offer 3 options
        if from_country.alpha_2 == to_country.alpha_2:
            options.append(
                ShippingOption(
                    id="fedex_ground",
                    name="FedEx Ground",
                    method_type=ShippingMethodType.STANDARD,
                    price=Money(amount="12.99", currency="USD"),
                    estimated_delivery_min=3,
                    estimated_delivery_max=5,
                    carrier="FedEx",
                    carrier_service_code="GROUND",
                    description="Standard delivery in 3-5 business days",
                )
            )

            options.append(
                ShippingOption(
                    id="fedex_express",
                    name="FedEx Express",
                    method_type=ShippingMethodType.EXPRESS,
                    price=Money(amount="19.99", currency="USD"),
                    estimated_delivery_min=2,
                    estimated_delivery_max=3,
                    carrier="FedEx",
                    carrier_service_code="EXPRESS",
                    description="Express delivery in 2-3 business days",
                )
            )

            options.append(
                ShippingOption(
                    id="fedex_overnight",
                    name="FedEx Overnight",
                    method_type=ShippingMethodType.NEXT_DAY,
                    price=Money(amount="29.99", currency="USD"),
                    estimated_delivery_min=1,
                    estimated_delivery_max=1,
                    carrier="FedEx",
                    carrier_service_code="OVERNIGHT",
                    description="Next day delivery",
                )
            )
        else:
            # For international shipments, offer international option
            options.append(
                ShippingOption(
                    id="fedex_international",
                    name="FedEx International",
                    method_type=ShippingMethodType.INTERNATIONAL,
                    price=Money(amount="49.99", currency="USD"),
                    estimated_delivery_min=5,
                    estimated_delivery_max=7,
                    carrier="FedEx",
                    carrier_service_code="INTERNATIONAL_ECONOMY",
                    description="International delivery in 5-7 business days",
                )
            )

        return options

    def create_shipment(
        self,
        order_id: str,
        shipping_option_id: str,
        address_data: dict[str, Any],
        **kwargs,
    ) -> tuple[bool, dict[str, Any]]:
        try:
            logger.info(
                "Creating FedEx shipment",
                extra={
                    "order_id": order_id,
                    "shipping_option_id": shipping_option_id,
                },
            )

            shipment_data = {
                "tracking_number": f"FX{order_id}12345",
                "label_url": f"https://example.com/labels/fedex_{order_id}.pdf",
                "carrier": "FedEx",
                "service": shipping_option_id,
                "created_at": timezone.now().isoformat(),
                "estimated_delivery": (
                    timezone.now().date() + timedelta(days=3)
                ).isoformat(),
            }

            return True, shipment_data

        except Exception as e:
            logger.error(
                f"FedEx shipment creation failed: {e!s}",
                extra={"order_id": order_id, "error": str(e)},
            )
            return False, {"error": str(e)}

    def get_tracking_info(self, tracking_number: str) -> dict[str, Any]:
        try:
            logger.info(
                "Getting FedEx tracking info",
                extra={"tracking_number": tracking_number},
            )

            # @TODO - Mock response
            current_date = timezone.now().date()

            tracking_data = {
                "tracking_number": tracking_number,
                "status": "IN_TRANSIT",
                "estimated_delivery": (
                    current_date + timedelta(days=2)
                ).isoformat(),
                "carrier": "FedEx",
                "events": [
                    {
                        "timestamp": (
                            current_date - timedelta(days=1)
                        ).isoformat(),
                        "location": "Memphis, TN",
                        "description": "Arrived at FedEx hub",
                        "status": "IN_TRANSIT",
                    },
                    {
                        "timestamp": (
                            current_date - timedelta(days=2)
                        ).isoformat(),
                        "location": "Atlanta, GA",
                        "description": "Shipment picked up",
                        "status": "PICKED_UP",
                    },
                ],
            }

            return tracking_data

        except Exception as e:
            logger.error(
                f"Failed to get FedEx tracking info: {e!s}",
                extra={"tracking_number": tracking_number, "error": str(e)},
            )
            return {"error": str(e)}


class UPSCarrier(ShippingCarrier):
    def __init__(self):
        self.api_key = settings.UPS_API_KEY
        self.account_number = settings.UPS_ACCOUNT_NUMBER

        # @TODO - In a real implementation, we would import and use UPS SDK

    def get_shipping_options(
        self,
        weight: Decimal,
        dimensions: dict[str, Decimal],
        from_country: Country,
        to_country: Country,
        to_region: Region | None = None,
        to_postal_code: str | None = None,
        **kwargs,
    ) -> list[ShippingOption]:
        logger.info(
            "Getting UPS shipping options",
            extra={
                "weight": str(weight),
                "from_country": from_country.alpha_2,
                "to_country": to_country.alpha_2,
                "to_postal_code": to_postal_code,
            },
        )

        options = []

        options.append(
            ShippingOption(
                id="ups_worldwide",
                name="UPS Worldwide Expedited",
                method_type=ShippingMethodType.INTERNATIONAL,
                price=Money(amount="47.99", currency="USD"),
                estimated_delivery_min=4,
                estimated_delivery_max=6,
                carrier="UPS",
                carrier_service_code="WWEX",
                description="International delivery in 4-6 business days",
            )
        )

        return options

    def create_shipment(
        self,
        order_id: str,
        shipping_option_id: str,
        address_data: dict[str, Any],
        **kwargs,
    ) -> tuple[bool, dict[str, Any]]:
        try:
            logger.info(
                "Creating UPS shipment",
                extra={
                    "order_id": order_id,
                    "shipping_option_id": shipping_option_id,
                },
            )

            # @TODO - Mock response
            shipment_data = {
                "tracking_number": f"1Z{order_id}789",
                "label_url": f"https://example.com/labels/ups_{order_id}.pdf",
                "carrier": "UPS",
                "service": shipping_option_id,
                "created_at": timezone.now().isoformat(),
                "estimated_delivery": (
                    timezone.now().date() + timedelta(days=3)
                ).isoformat(),
            }

            return True, shipment_data

        except Exception as e:
            logger.error(
                f"UPS shipment creation failed: {e!s}",
                extra={"order_id": order_id, "error": str(e)},
            )
            return False, {"error": str(e)}

    def get_tracking_info(self, tracking_number: str) -> dict[str, Any]:
        try:
            logger.info(
                "Getting UPS tracking info",
                extra={"tracking_number": tracking_number},
            )

            # @TODO - Mock response
            current_date = timezone.now().date()

            tracking_data = {
                "tracking_number": tracking_number,
                "status": "IN_TRANSIT",
                "estimated_delivery": (
                    current_date + timedelta(days=1)
                ).isoformat(),
                "carrier": "UPS",
                "events": [
                    {
                        "timestamp": (
                            current_date - timedelta(days=1)
                        ).isoformat(),
                        "location": "Louisville, KY",
                        "description": "Arrived at UPS facility",
                        "status": "IN_TRANSIT",
                    },
                    {
                        "timestamp": (
                            current_date - timedelta(days=2)
                        ).isoformat(),
                        "location": "Nashville, TN",
                        "description": "Shipment picked up",
                        "status": "PICKED_UP",
                    },
                ],
            }

            return tracking_data

        except Exception as e:
            logger.error(
                f"Failed to get UPS tracking info: {e!s}",
                extra={"tracking_number": tracking_number, "error": str(e)},
            )
            return {"error": str(e)}


def get_shipping_carrier(carrier_name: str) -> ShippingCarrier:
    carriers = {
        "fedex": FedExCarrier,
        "ups": UPSCarrier,
    }

    carrier_class = carriers.get(carrier_name.lower())
    if not carrier_class:
        raise ValueError(f"Unsupported shipping carrier: {carrier_name}")

    return carrier_class()


class ShippingService:
    @classmethod
    def get_available_shipping_options(
        cls,
        order_weight: Decimal,
        order_dimensions: dict[str, Decimal],
        from_country: Country,
        to_country: Country,
        to_region: Region | None = None,
        to_postal_code: str | None = None,
        **kwargs,
    ) -> list[ShippingOption]:
        all_options = []

        for carrier_name in ["fedex", "ups"]:
            try:
                carrier = get_shipping_carrier(carrier_name)
                options = carrier.get_shipping_options(
                    weight=order_weight,
                    dimensions=order_dimensions,
                    from_country=from_country,
                    to_country=to_country,
                    to_region=to_region,
                    to_postal_code=to_postal_code,
                    **kwargs,
                )
                all_options.extend(options)
            except Exception as e:
                logger.error(
                    f"Error getting shipping options from {carrier_name}: {e!s}",
                    extra={"error": str(e)},
                )

        return sorted(all_options, key=lambda x: x.price.amount)

    @classmethod
    def create_shipment(
        cls,
        order_id: str,
        carrier: str,
        shipping_option_id: str,
        address_data: dict[str, Any],
        **kwargs,
    ) -> tuple[bool, dict[str, Any]]:
        try:
            carrier_instance = get_shipping_carrier(carrier)
            return carrier_instance.create_shipment(
                order_id=order_id,
                shipping_option_id=shipping_option_id,
                address_data=address_data,
                **kwargs,
            )
        except Exception as e:
            logger.error(
                f"Error creating shipment: {e!s}",
                extra={
                    "order_id": order_id,
                    "carrier": carrier,
                    "error": str(e),
                },
            )
            return False, {"error": str(e)}

    @classmethod
    def get_tracking_info(
        cls, tracking_number: str, carrier: str
    ) -> dict[str, Any]:
        try:
            carrier_instance = get_shipping_carrier(carrier)
            return carrier_instance.get_tracking_info(tracking_number)
        except Exception as e:
            logger.error(
                f"Error getting tracking info: {e!s}",
                extra={
                    "tracking_number": tracking_number,
                    "carrier": carrier,
                    "error": str(e),
                },
            )
            return {"error": str(e)}
