from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from djmoney.money import Money
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
    TranslatableModelSerializer, serializers.ModelSerializer[PayWay]
):
    translations = TranslatedFieldsFieldExtend(shared_model=PayWay)
    cost = MoneyField(max_digits=11, decimal_places=2)
    free_threshold = MoneyField(max_digits=11, decimal_places=2)

    class Meta:
        model = PayWay
        fields = (
            "translations",
            "id",
            "active",
            "cost",
            "free_threshold",
            "icon",
            "sort_order",
            "created_at",
            "updated_at",
            "uuid",
            "icon_filename",
            "provider_code",
            "is_online_payment",
            "requires_confirmation",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "uuid",
            "icon_filename",
        )


class PayWayDetailSerializer(PayWaySerializer):
    class Meta(PayWaySerializer.Meta):
        fields = (
            *PayWaySerializer.Meta.fields,
            "configuration",
        )


class PayWayWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[PayWay]
):
    translations = TranslatedFieldsFieldExtend(shared_model=PayWay)
    cost = MoneyField(max_digits=11, decimal_places=2)
    free_threshold = MoneyField(max_digits=11, decimal_places=2, required=False)

    def validate_cost(self, value: Money) -> Money:
        if value and value.amount < 0:
            raise serializers.ValidationError(_("Cost cannot be negative."))
        return value

    def validate_free_threshold(self, value: Money) -> Money:
        if value and value.amount < 0:
            raise serializers.ValidationError(
                _("Free order amount threshold cannot be negative.")
            )
        return value

    def validate(self, data):
        if data.get("is_online_payment") and not data.get("provider_code"):
            raise serializers.ValidationError(
                _("Online payment methods must have a provider code.")
            )

        return data

    class Meta:
        model = PayWay
        fields = (
            "translations",
            "active",
            "cost",
            "free_threshold",
            "icon",
            "sort_order",
            "provider_code",
            "is_online_payment",
            "requires_confirmation",
            "configuration",
        )
