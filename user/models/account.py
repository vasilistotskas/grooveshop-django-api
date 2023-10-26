from __future__ import annotations

import os
from typing import List

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.sessions.models import Session
from django.db import models
from django.http import HttpRequest
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from phonenumber_field.modelfields import PhoneNumberField

from core import caches
from core.caches import cache_instance
from core.models import TimeStampMixinModel
from core.models import UUIDModel
from user.enum.account import UserRole

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
    phone = PhoneNumberField(_("Phone Number"), null=True, blank=True, default=None)
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
        sessions = Session.objects.filter(
            expire_date__gte=timezone.now(),
            session_data__contains=f'"_auth_user_id":{user_id}',
        )
        sessions.delete()

        # Session Cache
        user_cache_keys = cache_instance.keys(f"{caches.USER_AUTHENTICATED}{user_id}:*")
        django_cache_keys = cache_instance.keys("django.contrib.sessions.cache*")

        for key in user_cache_keys:
            cache_instance.delete(key)

        request.session.flush()

        for key in django_cache_keys:
            user = cache_instance.get(key)
            if not user:
                continue
            cache_user_id = user.get("_auth_user_id", None)
            if not cache_user_id:
                continue
            if user and int(user.get("_auth_user_id")) == int(user_id):
                cache_instance.delete(key)

    @staticmethod
    def remove_session(
        user: AbstractBaseUser | AnonymousUser, request: HttpRequest
    ) -> None:
        try:
            session = Session.objects.get(session_key=request.session.session_key)
            session.delete()
        except Session.DoesNotExist:
            pass

        # Session Cache
        if request.user.is_authenticated:
            cache_instance.delete(
                f"{caches.USER_AUTHENTICATED}{user.pk}:"
                f"{request.session.session_key}"
            )
        else:
            cache_instance.delete(
                f"{caches.USER_UNAUTHENTICATED}{request.session.session_key}"
            )

        request.session.flush()

        cache_instance.delete(
            f"django.contrib.sessions.cache{request.session.session_key}"
        )

    def get_cache(self) -> dict:
        user_cache_keys = cache_instance.keys(f"{caches.USER_AUTHENTICATED}{self.pk}:*")
        return cache_instance.get_many(keys=user_cache_keys)

    @property
    def role(self) -> str:
        if self.is_superuser:
            return UserRole.SUPERUSER
        elif self.is_staff:
            return UserRole.STAFF
        else:
            return UserRole.USER

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
                '<img src="{}" height="50"/>'.format("/static/images/default.png")
            )
