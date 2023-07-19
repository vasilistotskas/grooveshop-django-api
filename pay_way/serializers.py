from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer

from core.api.schema import generate_schema_multi_lang
from pay_way.models import PayWay


@extend_schema_field(generate_schema_multi_lang(PayWay))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class PayWaySerializer(TranslatableModelSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=PayWay)

    class Meta:
        model = PayWay
        fields = (
            "translations",
            "id",
            "active",
            "cost",
            "free_for_order_amount",
            "icon",
            "created_at",
            "updated_at",
            "sort_order",
            "uuid",
            "icon_absolute_url",
            "icon_filename",
        )
