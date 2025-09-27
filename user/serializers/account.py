from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class UserSerializer(serializers.ModelSerializer[User]):
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


class UserWriteSerializer(UserSerializer):
    phone = PhoneNumberField(required=False, allow_blank=True, allow_null=True)

    class Meta(UserSerializer.Meta):
        fields = (
            *UserSerializer.Meta.fields,
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


class UserDetailsSerializer(UserSerializer):
    phone = PhoneNumberField(required=False, allow_blank=True, allow_null=True)
    twitter = serializers.SerializerMethodField()
    linkedin = serializers.SerializerMethodField()
    facebook = serializers.SerializerMethodField()
    instagram = serializers.SerializerMethodField()
    website = serializers.SerializerMethodField()
    youtube = serializers.SerializerMethodField()
    github = serializers.SerializerMethodField()

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_twitter(self, obj) -> str | None:
        return obj.twitter

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_linkedin(self, obj) -> str | None:
        return obj.linkedin

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_facebook(self, obj) -> str | None:
        return obj.facebook

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_instagram(self, obj) -> str | None:
        return obj.instagram

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_website(self, obj) -> str | None:
        return obj.website

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_youtube(self, obj) -> str | None:
        return obj.youtube

    @extend_schema_field(
        {
            "type": "string",
            "nullable": True,
            "maxLength": 200,
            "description": _("URL link or empty string"),
        }
    )
    def get_github(self, obj) -> str | None:
        return obj.github

    class Meta(UserSerializer.Meta):
        fields = (
            *UserSerializer.Meta.fields,
            "id",
            "email",
            "username",
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


class UsernameUpdateSerializer(serializers.Serializer):
    username = serializers.CharField(
        max_length=150,
        help_text=_("New username"),
    )


class UsernameUpdateResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(
        help_text=_("Success message for username update")
    )


class UserSubscriptionSummaryResponseSerializer(serializers.Serializer):
    total_subscriptions = serializers.IntegerField()
    active_subscriptions = serializers.IntegerField()
    categories = serializers.ListField(child=serializers.CharField())
