from django.conf import settings
from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

User = get_user_model()


class UserDetailsSerializer(serializers.ModelSerializer[User]):
    def validate_username(self, username):
        if "allauth.account" not in settings.INSTALLED_APPS:
            return username

        from allauth.account.adapter import get_adapter  # noqa: PLC0415

        username = get_adapter().clean_username(username)
        return username

    class Meta:
        extra_fields = []
        if hasattr(User, "USERNAME_FIELD"):
            extra_fields.append(User.USERNAME_FIELD)
        if hasattr(User, "EMAIL_FIELD"):
            extra_fields.append(User.EMAIL_FIELD)
        if hasattr(User, "first_name"):
            extra_fields.append("first_name")
        if hasattr(User, "last_name"):
            extra_fields.append("last_name")
        model = User
        fields = ("pk", *extra_fields)
        read_only_fields = ("email",)


class AuthenticationSerializer(UserDetailsSerializer):
    phone = PhoneNumberField(required=False)

    class Meta(UserDetailsSerializer.Meta):
        fields = (
            *UserDetailsSerializer.Meta.fields,
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
            "full_name",
            "main_image_path",
        )
