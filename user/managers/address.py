from __future__ import annotations

from django.db import models


class UserAddressManager(models.Manager):
    def get_user_addresses(self, user):
        return self.filter(user=user).order_by("-is_main", "title")

    def get_main_address(self, user):
        return self.filter(user=user, is_main=True).first()
