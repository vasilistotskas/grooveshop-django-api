from django.db import models


class ContactQuerySet(models.QuerySet):
    def by_email_domain(self, domain):
        return self.filter(email__iendswith=f"@{domain}")


class ContactManager(models.Manager):
    def get_queryset(self) -> ContactQuerySet:
        return ContactQuerySet(self.model, using=self._db)

    def by_email_domain(self, domain):
        return self.get_queryset().by_email_domain(domain)
