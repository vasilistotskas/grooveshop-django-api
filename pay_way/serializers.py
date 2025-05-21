from djmoney.contrib.django_rest_framework import MoneyField
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from pay_way.models import PayWay


@extend_schema_field(generate_schema_multi_lang(PayWay))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class PayWaySerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    translations = TranslatedFieldsFieldExtend(shared_model=PayWay)
    cost = MoneyField(max_digits=11, decimal_places=2)
    free_for_order_amount = MoneyField(max_digits=11, decimal_places=2)

    class Meta:
        model = PayWay
        fields = (
            "translations",
            "id",
            "active",
            "cost",
            "free_for_order_amount",
            "icon",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "icon_absolute_url",
            "icon_filename",
            "provider_code",
            "is_online_payment",
            "requires_confirmation",
            "configuration",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
            "icon_absolute_url",
            "icon_filename",
        )
