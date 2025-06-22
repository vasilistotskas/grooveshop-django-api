from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth.models import BaseUserManager

from core.generators import UserNameGenerator

if TYPE_CHECKING:
    from user.models import UserAccount


class UserAccountManager(BaseUserManager["UserAccount"]):
    def active(self):
        return self.filter(is_active=True)

    def inactive(self):
        return self.filter(is_active=False)

    def staff(self):
        return self.filter(is_staff=True)

    def create_user(
        self, email: str, password: str, **extra_fields
    ) -> UserAccount:
        if not email:
            raise ValueError("Users must have an email address")
        new_email: str = self.normalize_email(email)
        username = extra_fields.pop(
            "username", None
        ) or UserNameGenerator().generate_username(new_email)
        user = self.model(email=new_email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def _create_user(
        self, email: str, password: str, **extra_fields
    ) -> UserAccount:
        if not email:
            raise ValueError("Users must have an email address")
        new_email: str = self.normalize_email(email)
        username = extra_fields.pop(
            "username", None
        ) or UserNameGenerator().generate_username(new_email)
        user = self.model(email=new_email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self, email: str, password: str, **extra_fields
    ) -> UserAccount:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if not extra_fields.get("is_staff"):
            raise ValueError("Superuser must have is_staff=True.")
        if not extra_fields.get("is_superuser"):
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)
