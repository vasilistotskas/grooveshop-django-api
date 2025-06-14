from __future__ import annotations

from django.db import models


class FavouriteQuerySet(models.QuerySet):
    def by_user(self, user_id: int):
        return self.filter(user_id=user_id)

    def by_product(self, product_id: int):
        return self.filter(product_id=product_id)


class FavouriteManager(models.Manager):
    def get_queryset(self) -> FavouriteQuerySet:
        return FavouriteQuerySet(self.model, using=self._db)

    def by_user(self, user_id: int):
        return self.get_queryset().by_user(user_id)

    def by_product(self, product_id: int):
        return self.get_queryset().by_product(product_id)

    def for_user(self, user):
        return self.get_queryset().filter(user=user)

    def for_product(self, product):
        return self.get_queryset().filter(product=product)
