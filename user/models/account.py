from __future__ import annotations

import os
from typing import List

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.sessions.models import Session
from django.db import models
from django.http import HttpRequest
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core import caches
from core.caches import cache_instance
from core.models import TimeStampMixinModel
from core.models import UUIDModel


User = settings.AUTH_USER_MODEL


class UserAccountManager(BaseUserManager):
    def create_user(self, email, password, **extra_fields) -> UserAccount:
        """Create and save a user with the given username, email, and password."""
        if not email:
            raise ValueError("Users must have an email address")
        email: str = self.normalize_email(email)
        user: UserAccount = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def _create_user(self, email, password, **extra_fields) -> UserAccount:
        """Create and save a user with the given username, email, and password."""
        if not email:
            raise ValueError("Users must have an email address")
        email: str = self.normalize_email(email)
        user: UserAccount = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields) -> UserAccount:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class UserAccount(AbstractBaseUser, PermissionsMixin, UUIDModel, TimeStampMixinModel):
    id = models.BigAutoField(primary_key=True)
    email = models.EmailField(_("Email Address"), max_length=254, unique=True)
    first_name = models.CharField(
        _("First Name"), max_length=255, blank=True, null=True
    )
    last_name = models.CharField(_("Last Name"), max_length=255, blank=True, null=True)
    phone = models.CharField(_("Phone"), max_length=255, blank=True, null=True)
    city = models.CharField(_("City"), max_length=255, blank=True, null=True)
    zipcode = models.CharField(_("Zip Code"), max_length=255, blank=True, null=True)
    address = models.CharField(_("Address"), max_length=255, blank=True, null=True)
    place = models.CharField(_("Place"), max_length=255, blank=True, null=True)
    country = models.ForeignKey(
        "country.Country",
        related_name="user_account_country",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="user_account_region",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    image = models.ImageField(
        _("Image"), upload_to="uploads/users/", blank=True, null=True
    )
    is_active = models.BooleanField(_("Active"), default=True)
    is_staff = models.BooleanField(_("Staff"), default=False)
    birth_date = models.DateField(_("Birth Date"), blank=True, null=True)

    objects: UserAccountManager = UserAccountManager()

    USERNAME_FIELD: str = "email"
    REQUIRED_FIELDS: List[str] = []

    class Meta(TypedModelMeta):
        verbose_name = _("User Account")
        verbose_name_plural = _("User Accounts")
        ordering = ["-created_at"]

    def __str__(self):
        return self.email

    def remove_all_sessions(self, request: HttpRequest) -> None:
        # Get the user's primary key
        user_id = self.pk

        # Delete all sessions associated with the user's primary key
        Session.objects.filter(
            expire_date__gte=timezone.now(),
            session_data__contains=f'auth_user_id": "{user_id}"',
        ).delete()

        # Session Cache
        user_cache_keys = cache_instance.keys(
            f"{caches.USER_AUTHENTICATED}_*{user_id}_*"
        )
        for key in user_cache_keys:
            cache_instance.delete(key)

        # Clear the cache for the current user
        user_cache_key = caches.USER_AUTHENTICATED + "_" + str(user_id)
        cache_instance.delete(user_cache_key)

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.APP_BASE_URL + self.image.url
        return image

    @property
    def main_image_filename(self) -> str:
        if self.image and hasattr(self.image, "name"):
            return os.path.basename(self.image.name)
        else:
            return ""

    @property
    def image_tag(self):
        if self.image and hasattr(self.image, "url"):
            return mark_safe('<img src="{}" height="50"/>'.format(self.image.url))
        else:
            return mark_safe(
                '<img src="{}" height="50"/>'.format("/files/images/default.png")
            )
