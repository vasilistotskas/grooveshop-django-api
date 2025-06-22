from __future__ import annotations

import os

from django.conf import settings
from django.contrib.auth.models import (
    AbstractBaseUser,
    PermissionsMixin,
)
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from phonenumber_field.modelfields import PhoneNumberField

from core.fields.image import ImageAndSvgField
from core.models import TimeStampMixinModel, UUIDModel
from user.managers.account import UserAccountManager
from user.models.subscription import UserSubscription


class UserAccount(
    AbstractBaseUser, PermissionsMixin, UUIDModel, TimeStampMixinModel
):
    username_validator = UnicodeUsernameValidator()

    id = models.BigAutoField(primary_key=True)
    username = models.CharField(
        _("Username"),
        max_length=settings.ACCOUNT_USERNAME_MAX_LENGTH,
        unique=True,
        blank=True,
        null=True,
        help_text=_(
            f"Required. {settings.ACCOUNT_USERNAME_MAX_LENGTH} characters or fewer."
            "Letters, digits and @/./+/-/_ only."
        ),
        validators=[username_validator],
        error_messages={
            "unique": _("A user with that username already exists."),
        },
    )
    email = models.EmailField(_("Email Address"), max_length=254, unique=True)
    first_name = models.CharField(
        _("First Name"), max_length=255, blank=True, default=""
    )
    last_name = models.CharField(
        _("Last Name"), max_length=255, blank=True, default=""
    )
    phone = PhoneNumberField(
        _("Phone Number"), null=True, blank=True, default=None
    )
    city = models.CharField(_("City"), max_length=255, blank=True, default="")
    zipcode = models.CharField(
        _("Zip Code"), max_length=255, blank=True, default=""
    )
    address = models.CharField(
        _("Address"), max_length=255, blank=True, default=""
    )
    place = models.CharField(_("Place"), max_length=255, blank=True, default="")
    country = models.ForeignKey(
        "country.Country",
        related_name="residents",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="residents",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    image = ImageAndSvgField(
        _("Image"), upload_to="uploads/users/", blank=True, null=True
    )
    is_active = models.BooleanField(_("Active"), default=True)
    is_staff = models.BooleanField(_("Staff"), default=False)
    birth_date = models.DateField(_("Birth Date"), blank=True, null=True)
    twitter = models.URLField(_("Twitter Profile"), blank=True, default="")
    linkedin = models.URLField(_("LinkedIn Profile"), blank=True, default="")
    facebook = models.URLField(_("Facebook Profile"), blank=True, default="")
    instagram = models.URLField(_("Instagram Profile"), blank=True, default="")
    website = models.URLField(_("Website"), blank=True, default="")
    youtube = models.URLField(_("Youtube Profile"), blank=True, default="")
    github = models.URLField(_("Github Profile"), blank=True, default="")
    bio = models.TextField(_("Bio"), blank=True, default="")

    objects: UserAccountManager = UserAccountManager()

    USERNAME_FIELD: str = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta(TypedModelMeta):
        verbose_name = _("User Account")
        verbose_name_plural = _("User Accounts")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["email"], name="user_account_email_ix"),
            BTreeIndex(fields=["username"], name="user_account_username_ix"),
            BTreeIndex(fields=["is_active"], name="user_account_is_active_ix"),
            BTreeIndex(fields=["is_staff"], name="user_account_is_staff_ix"),
            BTreeIndex(fields=["country"], name="user_account_country_ix"),
            BTreeIndex(fields=["region"], name="user_account_region_ix"),
            GinIndex(
                name="user_account_identity_gin_ix",
                fields=["email", "username"],
                opclasses=["gin_trgm_ops"] * 2,
            ),
            GinIndex(
                name="user_account_name_gin_ix",
                fields=["first_name", "last_name"],
                opclasses=["gin_trgm_ops"] * 2,
            ),
            GinIndex(
                name="user_account_location_gin_ix",
                fields=["city", "zipcode", "address", "place"],
                opclasses=["gin_trgm_ops"] * 4,
            ),
        ]

    def __str__(self):
        return self.username if self.username else self.email

    @property
    def active_subscriptions(self):
        return self.subscriptions.filter(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        ).select_related("topic")

    @property
    def subscription_preferences(self):
        prefs = {}
        for sub in self.subscriptions.select_related("topic"):
            prefs[sub.topic.slug] = (
                sub.status == UserSubscription.SubscriptionStatus.ACTIVE
            )
        return prefs

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def main_image_path(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return f"media/uploads/users/{os.path.basename(self.image.name)}"
        return ""
