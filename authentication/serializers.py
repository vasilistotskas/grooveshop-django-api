from django.conf import settings
from django.contrib.auth import get_user_model
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class UserDetailsSerializer(serializers.ModelSerializer[User]):
    def validate_username(self, username: str) -> str:
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


class UserWriteSerializer(UserDetailsSerializer):
    phone = PhoneNumberField(required=False, allow_blank=True, allow_null=True)

    class Meta(UserDetailsSerializer.Meta):
        fields = (
            *UserDetailsSerializer.Meta.fields,
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
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    def validate_email(self, email):
        if self.instance and self.instance.email != email:
            if User.objects.filter(email=email).exists():
                raise serializers.ValidationError(
                    _("A user with this email already exists.")
                )
        return email

    def validate_username(self, username):
        username = super().validate_username(username)
        if self.instance and self.instance.username != username:
            if User.objects.filter(username=username).exists():
                raise serializers.ValidationError(
                    _("A user with this username already exists.")
                )
        return username


class AuthenticationSerializer(UserDetailsSerializer):
    phone = PhoneNumberField(required=False, allow_blank=True, allow_null=True)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("image") == "":
            data["image"] = None
        return data

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
