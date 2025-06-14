from __future__ import annotations

from parler.managers import TranslatableManager, TranslatableQuerySet

from product.enum.review import ReviewStatusEnum


class ReviewQuerySet(TranslatableQuerySet):
    def approved(self):
        return self.filter(status=ReviewStatusEnum.TRUE)

    def pending(self):
        return self.filter(status=ReviewStatusEnum.NEW)

    def rejected(self):
        return self.filter(status=ReviewStatusEnum.FALSE)


class ReviewManager(TranslatableManager):
    def get_queryset(self) -> ReviewQuerySet:
        return ReviewQuerySet(self.model, using=self._db)

    def approved(self):
        return self.get_queryset().approved()

    def pending(self):
        return self.get_queryset().pending()

    def rejected(self):
        return self.get_queryset().rejected()
