from drf_spectacular.utils import extend_schema_field
from parler_rest.fields import TranslatedFieldsField
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from notification.models.notification import Notification


@extend_schema_field(generate_schema_multi_lang(Notification))
class TranslatedFieldsFieldExtend(TranslatedFieldsField):
    pass


class NotificationSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer
):
    translations = TranslatedFieldsFieldExtend(shared_model=Notification)

    class Meta:
        model = Notification
        fields = (
            "translations",
            "id",
            "link",
            "kind",
            "expiry_date",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )
