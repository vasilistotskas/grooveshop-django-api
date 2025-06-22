from django.utils import timezone
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
            "id",
            "created_at",
            "updated_at",
            "uuid",
        )


class NotificationDetailSerializer(NotificationSerializer):
    class Meta(NotificationSerializer.Meta):
        fields = (*NotificationSerializer.Meta.fields,)


class NotificationWriteSerializer(
    TranslatableModelSerializer, serializers.ModelSerializer[Notification]
):
    translations = TranslatedFieldsFieldExtend(shared_model=Notification)

    def validate_expiry_date(self, value):
        if value and value <= timezone.now():
            raise serializers.ValidationError(
                _("Expiry date must be in the future.")
            )
        return value

    def validate_link(self, value):
        if value and not (
            value.startswith("http://")
            or value.startswith("https://")
            or value.startswith("/")
        ):
            raise serializers.ValidationError(
                _("Link must be a valid URL or relative path.")
            )
        return value

    class Meta:
        model = Notification
        fields = (
            "translations",
            "link",
            "kind",
            "expiry_date",
        )
