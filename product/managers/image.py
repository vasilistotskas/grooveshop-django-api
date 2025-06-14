from __future__ import annotations

from parler.managers import TranslatableManager, TranslatableQuerySet


class ImageQuerySet(TranslatableQuerySet):
    def main_image(self, product):
        return self.filter(product=product, is_main=True).first()


class ImageManager(TranslatableManager):
    def get_queryset(self) -> ImageQuerySet:
        return ImageQuerySet(self.model, using=self._db)

    def main_image(self, product):
        return self.get_queryset().main_image(product=product)
