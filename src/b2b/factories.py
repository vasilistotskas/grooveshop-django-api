from decimal import Decimal

import factory
from djmoney.money import Money

from b2b.enum import BusinessProfileStatus, ViesStatus
from b2b.models import BusinessProfile, CustomerGroup, PriceListItem
from devtools.factories import CustomDjangoModelFactory


def _greek_vat(n: int) -> str:
    """A checksum-valid 9-digit ΑΦΜ derived from a sequence number."""
    base = f"{100000000 + n:08d}"[:8]
    total = sum(int(base[i]) * (2 ** (8 - i)) for i in range(8))
    return base + str(total % 11 % 10)


class CustomerGroupFactory(CustomDjangoModelFactory):
    name = factory.Sequence(lambda n: f"Wholesale {n}")
    discount_percent = Decimal("10.00")
    is_active = True

    class Meta:
        model = CustomerGroup
        django_get_or_create = ("name",)


class BusinessProfileFactory(CustomDjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    status = BusinessProfileStatus.PENDING
    company_name = factory.Sequence(lambda n: f"Company {n} IKE")
    vat_id = factory.Sequence(_greek_vat)
    tax_office = "Α' Αθηνών"
    activity = "Retail trade"
    vies_status = ViesStatus.UNCHECKED

    class Meta:
        model = BusinessProfile

    class Params:
        approved = factory.Trait(
            status=BusinessProfileStatus.APPROVED,
            customer_group=factory.SubFactory(CustomerGroupFactory),
        )


class PriceListItemFactory(CustomDjangoModelFactory):
    group = factory.SubFactory(CustomerGroupFactory)
    product = factory.SubFactory("product.factories.product.ProductFactory")
    net_price = Money("10.00", "EUR")

    class Meta:
        model = PriceListItem
