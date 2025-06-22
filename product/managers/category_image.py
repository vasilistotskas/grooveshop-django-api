from __future__ import annotations

from parler.managers import TranslatableManager, TranslatableQuerySet

from product.enum.category import CategoryImageTypeEnum


class CategoryImageQuerySet(TranslatableQuerySet):
    def active(self):
        return self.filter(active=True)

    def by_category(self, category):
        return self.filter(category=category)

    def by_type(self, image_type: CategoryImageTypeEnum):
        return self.filter(image_type=image_type)

    def main_images(self):
        return self.filter(image_type=CategoryImageTypeEnum.MAIN)

    def banner_images(self):
        return self.filter(image_type=CategoryImageTypeEnum.BANNER)

    def icon_images(self):
        return self.filter(image_type=CategoryImageTypeEnum.ICON)

    def gallery_images(self):
        return self.filter(image_type=CategoryImageTypeEnum.GALLERY)

    def get_main_image(self, category):
        return self.filter(
            category=category,
            image_type=CategoryImageTypeEnum.MAIN,
            active=True,
        ).first()

    def get_banner_image(self, category):
        return self.filter(
            category=category,
            image_type=CategoryImageTypeEnum.BANNER,
            active=True,
        ).first()

    def get_icon_image(self, category):
        return self.filter(
            category=category,
            image_type=CategoryImageTypeEnum.ICON,
            active=True,
        ).first()

    def get_image_by_type(self, category, image_type: CategoryImageTypeEnum):
        return self.filter(
            category=category, image_type=image_type, active=True
        ).first()


class CategoryImageManager(TranslatableManager):
    def get_queryset(self) -> CategoryImageQuerySet:
        return CategoryImageQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()

    def by_category(self, category):
        return self.get_queryset().by_category(category)

    def by_type(self, image_type: CategoryImageTypeEnum):
        return self.get_queryset().by_type(image_type)

    def main_images(self):
        return self.get_queryset().main_images()

    def banner_images(self):
        return self.get_queryset().banner_images()

    def icon_images(self):
        return self.get_queryset().icon_images()

    def gallery_images(self):
        return self.get_queryset().gallery_images()

    def get_main_image(self, category):
        return self.get_queryset().get_main_image(category)

    def get_banner_image(self, category):
        return self.get_queryset().get_banner_image(category)

    def get_icon_image(self, category):
        return self.get_queryset().get_icon_image(category)

    def get_image_by_type(self, category, image_type: CategoryImageTypeEnum):
        return self.get_queryset().get_image_by_type(category, image_type)
