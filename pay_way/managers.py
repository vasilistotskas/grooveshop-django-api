from django.db import models


class PayWayQuerySet(models.QuerySet):
    def active(self):
        return self.filter(active=True)

    def inactive(self):
        return self.filter(active=False)

    def online_payments(self):
        return self.filter(is_online_payment=True)

    def offline_payments(self):
        return self.filter(is_online_payment=False)


class PayWayManager(models.Manager):
    def get_queryset(self) -> PayWayQuerySet:
        return PayWayQuerySet(self.model, using=self._db)

    def active(self):
        return self.get_queryset().active()

    def inactive(self):
        return self.get_queryset().inactive()

    def online_payments(self):
        return self.get_queryset().online_payments()

    def offline_payments(self):
        return self.get_queryset().offline_payments()
