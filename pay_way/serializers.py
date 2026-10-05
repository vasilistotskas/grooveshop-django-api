from django.utils.translation import gettext_lazy as _
from djmoney.contrib.django_rest_framework import MoneyField
from djmoney.money import Money
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from pay_way.enum.settlement import PaySettlement
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
            "key",
            "active",
            "cost",
            "free_threshold",
            "icon",
            "sort_order",
            "main_image_path",
            "created_at",
            "updated_at",
            "uuid",
            "icon_filename",
            "provider_code",
            "settlement",
            # Deprecated mirrors of ``settlement``, still emitted for
            # one release so the storefront can migrate off them
            # separately from the column drop. Read ``settlement``.
            "is_online_payment",
            "requires_confirmation",
        )
        read_only_fields = (
            "id",
            # Read-only here (writes go through PayWayWriteSerializer) so
            # the schema states what every row carries: a key. Readers
            # label the pay way from it and have nothing to fall back on.
            "key",
            "sort_order",
            "main_image_path",
            "created_at",
            "updated_at",
            "uuid",
            "icon_filename",
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

    def validate(self, attrs):
        # ``settlement`` is the only writable truth; the two booleans
        # are derived in ``PayWay.save()`` and are not accepted here.
        settlement = attrs.get("settlement") or getattr(
            self.instance, "settlement", None
        )
        if settlement == PaySettlement.ONLINE and not (
            attrs.get("provider_code")
            or getattr(self.instance, "provider_code", "")
        ):
            raise serializers.ValidationError(
                _("Online payment methods must have a provider code.")
            )

        return attrs

    class Meta:
        model = PayWay
        fields = (
            "translations",
            "key",
            "active",
            "cost",
            "free_threshold",
            "icon",
            "sort_order",
            "provider_code",
            "settlement",
        )
        read_only_fields = ("sort_order",)
        extra_kwargs = {"key": {"required": True}}
