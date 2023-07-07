from __future__ import annotations

import os
from typing import Any
from typing import List

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import models
from django.http import HttpRequest
from django.utils.safestring import mark_safe

from core import caches
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum

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
    email = models.EmailField(max_length=255, unique=True)
    first_name = models.CharField(max_length=255)
    last_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=100, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    zipcode = models.CharField(max_length=100, blank=True, null=True)
    address = models.CharField(max_length=100, blank=True, null=True)
    place = models.CharField(max_length=100, blank=True, null=True)
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
    image = models.ImageField(upload_to="uploads/users/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    birth_date = models.DateField(blank=True, null=True)

    objects: UserAccountManager = UserAccountManager()

    USERNAME_FIELD: str = "email"
    REQUIRED_FIELDS: List[str] = []

    def __str__(self):
        return self.email

    def remove_all_sessions(self, request: HttpRequest) -> None:
        # Session DB
        user_sessions: List[Any] = []
        for session in Session.objects.all():
            if str(self.pk) == session.get_decoded().get("_auth_user_id"):
                user_sessions.append(session.pk)
        Session.objects.filter(pk__in=user_sessions).delete()

        # Session Cache
        user_cache_keys = cache.keys(f"{caches.USER}_*{self.pk}_*")
        for key in user_cache_keys:
            caches.delete(key)

        return None

    @property
    def main_image_absolute_url(self) -> str:
        image: str = ""
        if self.image and hasattr(self.image, "url"):
            return settings.BACKEND_BASE_URL + self.image.url
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


class UserAddress(TimeStampMixinModel, UUIDModel):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(
        "user.UserAccount", related_name="address_user", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    street = models.CharField(max_length=100)
    street_number = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    zipcode = models.CharField(max_length=100)
    country = models.ForeignKey(
        "country.Country",
        related_name="address_country",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    region = models.ForeignKey(
        "region.Region",
        related_name="address_region",
        null=True,
        blank=True,
        default=None,
        on_delete=models.SET_NULL,
    )
    floor = models.CharField(
        max_length=50,
        choices=FloorChoicesEnum.choices(),
        null=True,
        blank=True,
        default=None,
    )
    location_type = models.CharField(
        max_length=100,
        choices=LocationChoicesEnum.choices(),
        null=True,
        blank=True,
        default=None,
    )
    phone = models.CharField(max_length=100, null=True, blank=True, default=None)
    mobile_phone = models.CharField(max_length=100, null=True, blank=True, default=None)
    notes = models.CharField(max_length=100, null=True, blank=True, default=None)
    is_main = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "User's Addresses"
        ordering = ["-is_main", "id"]

    def __str__(self):
        return self.title

    @classmethod
    def get_user_addresses(cls, user) -> models.QuerySet:
        return cls.objects.filter(user=user)

    @classmethod
    def get_main_address(cls, user) -> models.QuerySet:
        return cls.objects.filter(user=user, is_main=True).first()

    @classmethod
    def get_user_address_count(cls, user) -> int:
        return cls.objects.filter(user=user).count()
