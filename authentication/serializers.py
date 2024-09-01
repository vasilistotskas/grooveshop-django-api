from django.conf import settings
from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

UserModel = get_user_model()


class UserDetailsSerializer(serializers.ModelSerializer):
    @staticmethod
    def validate_username(username):
        if "allauth.account" not in settings.INSTALLED_APPS:
            return username

        from allauth.account.adapter import get_adapter

        username = get_adapter().clean_username(username)
        return username

    class Meta:
        extra_fields = []
        if hasattr(UserModel, "USERNAME_FIELD"):
            extra_fields.append(UserModel.USERNAME_FIELD)
        if hasattr(UserModel, "EMAIL_FIELD"):
            extra_fields.append(UserModel.EMAIL_FIELD)
        if hasattr(UserModel, "first_name"):
            extra_fields.append("first_name")
        if hasattr(UserModel, "last_name"):
            extra_fields.append("last_name")
        model = UserModel
        fields = ("pk", *extra_fields)
        read_only_fields = ("email",)


class UsernameUpdateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=30)


class AuthenticationSerializer(UserDetailsSerializer):
    phone = PhoneNumberField(required=False)

    class Meta(UserDetailsSerializer.Meta):
        fields = UserDetailsSerializer.Meta.fields + (
            "id",
            "email",
            "username",
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
            "birth_date",
            "twitter",
            "linkedin",
            "facebook",
            "instagram",
            "website",
            "youtube",
            "github",
            "bio",
            "is_active",
            "is_staff",
            "is_superuser",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )
        read_only_fields = (
            "is_active",
            "is_staff",
            "is_superuser",
            "created_at",
            "updated_at",
            "uuid",
            "main_image_path",
        )
