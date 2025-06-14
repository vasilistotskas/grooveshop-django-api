from django.db import models


class VatManager(models.Manager):
    def get_highest(self):
        return self.get_queryset().order_by("-value").first()

    def get_lowest(self):
        return self.get_queryset().order_by("value").first()
