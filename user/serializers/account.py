from django.http import HttpRequest
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

from user.models import UserAccount


class UserAccountSerializer(serializers.ModelSerializer):
    last_login = serializers.SerializerMethodField("get_last_login")
    phone = PhoneNumberField(required=False)

    @extend_schema_field(serializers.DateTimeField)
    def get_last_login(self, user_account) -> str or None:
        request: HttpRequest = self.context.get("request")
        try:
            last_login = request.session.get("last_login", None)
        except AttributeError:
            last_login = None
        return last_login

    class Meta:
        model = UserAccount
        fields = (
            "id",
            "email",
            "image",
            "first_name",
            "last_name",
            "phone",
            "city",
            "zipcode",
            "address",
            "place",
            "country",
            "region",
            "is_active",
            "is_staff",
            "birth_date",
            "main_image_absolute_url",
            "main_image_filename",
            "is_superuser",
            "last_login",
            "is_active",
            "created_at",
            "updated_at",
            "uuid",
        )
