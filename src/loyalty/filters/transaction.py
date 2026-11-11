from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters

from core.filters.camel_case_filters import CamelCaseTimeStampFilterSet
from core.filters.core import UUIDFilterMixin
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction


class PointsTransactionFilter(
    UUIDFilterMixin,
    CamelCaseTimeStampFilterSet,
):
    transaction_type = filters.ChoiceFilter(
        field_name="transaction_type",
        choices=TransactionType.choices,
        help_text=_(
            "Filter by transaction type (EARN, REDEEM, EXPIRE, ADJUST, BONUS)"
        ),
    )

    class Meta:
        model = PointsTransaction
        fields = {
            "transaction_type": ["exact"],
            "created_at": ["gte", "lte", "date"],
            "updated_at": ["gte", "lte", "date"],
            "uuid": ["exact"],
        }
