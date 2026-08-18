from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from parler_rest.serializers import TranslatableModelSerializer
from rest_framework import serializers

from core.api.schema import generate_schema_multi_lang
from core.utils.serializers import TranslatedFieldExtended
from notification.models.notification import Notification


@extend_schema_field(generate_schema_multi_lang(Notification))
class TranslatedFieldsFieldExtend(TranslatedFieldExtended):
    pass


class NotificationSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Notification]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Notification)
    link = serializers.SerializerMethodField()

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_link(self, obj) -> str | None:
        return obj.link

    class Meta:
        model = Notification
        fields = (
            "translations",
            "id",
            "link",
            "kind",
            "category",
            "priority",
            "notification_type",
            "expiry_date",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "uuid",
        )
