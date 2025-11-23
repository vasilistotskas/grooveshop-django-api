"""Sample data generator for email template previews."""

import random
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone


class SampleOrderDataGenerator:
    """
    Generates realistic sample order data for template previews.
    """

    # Greek sample data
    GREEK_FIRST_NAMES = [
        "Γιώργος",
        "Μαρία",
        "Δημήτρης",
        "Ελένη",
        "Νίκος",
        "Κατερίνα",
        "Κώστας",
        "Σοφία",
        "Παναγιώτης",
        "Άννα",
    ]

    GREEK_LAST_NAMES = [
        "Παπαδόπουλος",
        "Γεωργίου",
        "Δημητρίου",
        "Νικολάου",
        "Κωνσταντίνου",
        "Ιωάννου",
        "Παπαδάκης",
        "Αθανασίου",
    ]

    GREEK_STREETS = [
        "Ακαδημίας",
        "Πανεπιστημίου",
        "Σταδίου",
        "Ερμού",
        "Αθηνάς",
        "Πατησίων",
        "Βασιλίσσης Σοφίας",
        "Κηφισίας",
    ]

    GREEK_CITIES = [
        {"name": "Αθήνα", "zipcode": "10672"},
        {"name": "Θεσσαλονίκη", "zipcode": "54622"},
        {"name": "Πάτρα", "zipcode": "26221"},
        {"name": "Ηράκλειο", "zipcode": "71202"},
        {"name": "Λάρισα", "zipcode": "41222"},
    ]

    SAMPLE_PRODUCTS = [
        {"name": "Wireless Headphones", "price": Decimal("89.99")},
        {"name": "Smart Watch", "price": Decimal("199.99")},
        {"name": "Laptop Stand", "price": Decimal("45.50")},
        {"name": "USB-C Cable", "price": Decimal("12.99")},
        {"name": "Mechanical Keyboard", "price": Decimal("129.99")},
        {"name": "Wireless Mouse", "price": Decimal("34.99")},
        {"name": "Phone Case", "price": Decimal("19.99")},
        {"name": "Portable Charger", "price": Decimal("39.99")},
    ]

    CARRIERS = ["DHL Express", "ACS Courier", "ELTA Courier", "Speedex"]

    def generate_order(self) -> dict:
        """Generate complete sample order data."""
        order_data = self._generate_order_object()
        items_data = self._generate_order_items()

        # Calculate totals
        items_total = sum(item["total_price"] for item in items_data)
        shipping_price = (
            Decimal("5.00")
            if items_total < Decimal("50.00")
            else Decimal("0.00")
        )
        total_price = items_total + shipping_price

        order_data["total_price_items"] = f"€{items_total:.2f}"
        order_data["shipping_price"] = f"€{shipping_price:.2f}"
        order_data["total_price"] = f"€{total_price:.2f}"
        order_data["paid_amount"] = f"€{total_price:.2f}"

        return {
            "order": order_data,
            "items": items_data,
            "tracking_number": self._generate_tracking_number(),
            "carrier": random.choice(self.CARRIERS),
        }

    def _generate_order_object(self) -> dict:
        """Generate sample Order model data."""
        city_data = random.choice(self.GREEK_CITIES)
        created_at = timezone.now() - timedelta(days=random.randint(0, 7))

        return {
            "id": random.randint(10000, 99999),
            "uuid": "550e8400-e29b-41d4-a716-446655440000",
            "status": "PENDING",
            "first_name": random.choice(self.GREEK_FIRST_NAMES),
            "last_name": random.choice(self.GREEK_LAST_NAMES),
            "email": f"customer{random.randint(1, 999)}@example.com",
            "phone": f"+30 210 {random.randint(1000000, 9999999)}",
            "street": random.choice(self.GREEK_STREETS),
            "street_number": str(random.randint(1, 200)),
            "city": city_data["name"],
            "zipcode": city_data["zipcode"],
            "country": "Greece",
            "created_at": created_at,
            "status_updated_at": timezone.now(),
            "tracking_number": self._generate_tracking_number(),
            "shipping_carrier": random.choice(self.CARRIERS),
        }

    def _generate_order_items(self) -> list[dict]:
        """Generate sample OrderItem data."""
        num_items = random.randint(1, 4)
        selected_products = random.sample(self.SAMPLE_PRODUCTS, num_items)

        items = []
        for idx, product_data in enumerate(selected_products, 1):
            quantity = random.randint(1, 3)
            price = product_data["price"]
            total_price = price * quantity

            items.append(
                {
                    "id": idx,
                    "product": {
                        "id": idx,
                        "name": product_data["name"],
                    },
                    "quantity": quantity,
                    "price": price,
                    "price_formatted": f"€{price:.2f}",
                    "total_price": total_price,
                    "total_price_formatted": f"€{total_price:.2f}",
                    "get_total_price": f"€{total_price:.2f}",
                }
            )

        return items

    def _generate_tracking_number(self) -> str:
        """Generate a realistic tracking number."""
        prefix = random.choice(["1Z", "9V", "7E"])
        numbers = "".join([str(random.randint(0, 9)) for _ in range(16)])
        return f"{prefix}{numbers}"

    def get_status_display(self, status: str) -> str:
        """Get display name for order status."""
        status_display = {
            "PENDING": "Pending",
            "PROCESSING": "Processing",
            "SHIPPED": "Shipped",
            "DELIVERED": "Delivered",
            "COMPLETED": "Completed",
            "CANCELED": "Canceled",
            "REFUNDED": "Refunded",
            "RETURNED": "Returned",
        }
        return status_display.get(status, status)
