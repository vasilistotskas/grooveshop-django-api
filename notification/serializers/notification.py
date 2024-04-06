from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer

from core.api.schema import generate_schema_multi_lang
from core.api.serializers import BaseExpandSerializer
from notification.models.notification import Notification


@extend_schema_field(generate_schema_multi_lang(Notification))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class NotificationSerializer(TranslatableModelSerializer, BaseExpandSerializer):
    translations = TranslatedFieldsFieldExtend(shared_model=Notification)

    class Meta:
        model = Notification
        fields = (
            "translations",
            "id",
            "created_at",
            "updated_at",
            "uuid",
            "link",
            "kind",
        )
