from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers

if TYPE_CHECKING:
    from user.models.account import UserAccount as User
else:
    User = get_user_model()


class UserSerializer(serializers.ModelSerializer[User]):
    def validate_username(self, username: str) -> str:
        if "allauth.account" not in settings.INSTALLED_APPS:
            return username

        from allauth.account.adapter import get_adapter

        username = get_adapter().clean_username(username)
        return username

    class Meta:
        # Named outright. AUTH_USER_MODEL is fixed to user.UserAccount,
        # whose USERNAME_FIELD is "email", so the hasattr probes that
        # used to build this list resolved to exactly these every time —
        # except the EMAIL_FIELD probe, which silently never matched
        # (UserAccount extends AbstractBaseUser, which does not define
        # it). A shim for a pluggable user model that cannot be plugged.
        model = User
        fields = ("pk", "email", "first_name", "last_name")
        read_only_fields = ("email",)


class UserWriteSerializer(UserSerializer):
    phone = PhoneNumberField(required=False, allow_blank=True, allow_null=True)

    class Meta(UserSerializer.Meta):
        # The base fields are spread in, so they are not repeated here —
        # the dynamic list they used to be built from made "email",
        # "first_name" and "last_name" appear twice in this tuple.
        fields = (
            *UserSerializer.Meta.fields,
            "username",
            "image",
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
            "language_code",
        )
        read_only_fields = (
            "created_at",
            "updated_at",
            "uuid",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Email is settable at registration (create) but read-only afterwards.
        # Changing the primary email must go through allauth's email-management
        # flow, which sends a verification link and updates the EmailAddress
        # source-of-truth; a plain profile PUT/PATCH must not silently change
        # it (that would bypass verification and desync allauth's EmailAddress
        # table).
        if self.instance is not None and "email" in self.fields:
            self.fields["email"].read_only = True

    def validate_language_code(self, value: str) -> str:
        if not value:
            return settings.LANGUAGE_CODE
        valid = {code for code, _name in settings.LANGUAGES}
        if value not in valid:
            raise serializers.ValidationError(_("Unsupported language code."))
        return value

    def validate_username(self, username):
        username = super().validate_username(username)
        if (self.instance and self.instance.username != username) and (
            User.objects.filter(username=username).exists()
        ):
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
            "language_code",
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


class UserPublicSerializer(serializers.ModelSerializer):
    """The author identity shown to anyone, including anonymous callers.

    `UserDetailsSerializer` is the ACCOUNT serializer — it carries
    `email`, `phone`, `address`, `city`, `zipcode`, `birth_date` and the
    privilege flags, which is right for "my account" and catastrophic
    anywhere else. It was nested as the `user` field on product reviews,
    blog comments (including parent and ancestor comments) and blog
    authors, all of which serve anonymous readers — so an unauthenticated
    walk of `/api/v1/product/review` returned a full contact record for
    every customer who had ever left one, and the blog-author route did
    the same for store personnel.

    `read_only_fields` does not help: it stops a field being WRITTEN, not
    rendered.

    This exposes only what a byline needs. The storefront reads exactly
    `id`, `username`, `firstName` and `lastName` on these surfaces, so
    nothing here is a display regression.
    """

    main_image_path = serializers.SerializerMethodField()

    @extend_schema_field(
        {"type": "string", "description": _("Avatar path or empty string")}
    )
    def get_main_image_path(self, obj) -> str:
        return getattr(obj, "main_image_path", "") or ""

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "main_image_path",
        )
        read_only_fields = fields


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


class UserDataExportSerializer(serializers.Serializer):
    """Read-only view of a UserDataExport row for the privacy UI."""

    id = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    token = serializers.CharField(read_only=True)
    file_size = serializers.IntegerField(read_only=True, allow_null=True)
    expires_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    download_url = serializers.SerializerMethodField()

    def get_download_url(self, obj) -> str | None:
        from user.models.data_export import UserDataExport

        if obj.status != UserDataExport.Status.READY:
            return None
        request = self.context.get("request")
        path = f"/api/v1/user/data_export/{obj.token}/download"
        if request is not None:
            return request.build_absolute_uri(path)
        return path


class DeleteAccountRequestSerializer(serializers.Serializer):
    """Body for ``POST user/account/{id}/delete_account``.

    Requires the user to re-type ``DELETE`` as a guardrail. The allauth
    re-authentication happens outside this serializer via the session
    middleware's ``X-Session-Token`` header before the task is queued.
    """

    confirmation = serializers.CharField(
        help_text=_('Must equal the literal string "DELETE".')
    )

    def validate_confirmation(self, value: str) -> str:
        if value != "DELETE":
            raise serializers.ValidationError(
                _("Type DELETE exactly to confirm account deletion.")
            )
        return value


class DeleteAccountResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
