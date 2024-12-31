from __future__ import annotations

import os

from django.conf import settings
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from phonenumber_field.modelfields import PhoneNumberField

from core.fields.image import ImageAndSvgField
from core.generators import UserNameGenerator
from core.models import TimeStampMixinModel, UUIDModel
from user.enum.account import UserRole

User = settings.AUTH_USER_MODEL


class UserAccountManager(BaseUserManager):
    def create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email: str = self.normalize_email(email)
        username = extra_fields.pop(
            "username", None
        ) or UserNameGenerator().generate_username(email)
        user: UserAccount = self.model(
            email=email, username=username, **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email: str = self.normalize_email(email)
        username = extra_fields.pop(
            "username", None
        ) or UserNameGenerator().generate_username(email)
        user: UserAccount = self.model(
            email=email, username=username, **extra_fields
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


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
            GinIndex(
                name="user_account_trgm_idx",
                fields=[
                    "email",
                    "username",
                    "first_name",
                    "last_name",
                    "phone",
                    "city",
                    "zipcode",
                    "address",
                    "place",
                ],
                opclasses=["gin_trgm_ops"] * 9,
            ),
        ]

    def __str__(self):
        return self.username if self.username else self.email

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def role(self):
        if self.is_superuser:
            return UserRole.SUPERUSER
        elif self.is_staff:
            return UserRole.STAFF
        else:
            return UserRole.USER

    @property
    def main_image_path(self):
        if self.image and hasattr(self.image, "name"):
            return f"media/uploads/users/{os.path.basename(self.image.name)}"
        return ""

    @property
    def image_tag(self):
        if self.image and hasattr(self.image, "url"):
            return mark_safe(
                '<img src="{}" height="50"/>'.format(self.image.url)
            )
        else:
            return mark_safe(
                '<img src="{}" height="50"/>'.format(
                    "/static/images/default.png"
                )
            )
