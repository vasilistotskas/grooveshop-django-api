from django.db import models


class RegionManager(models.Manager):
    def active(self):
        return self.get_queryset()

    def by_country(self, country_code):
        return self.get_queryset().filter(country__alpha_2=country_code.upper())

    def by_country_name(self, country_name):
        return self.get_queryset().filter(
            country__translations__name__icontains=country_name
        )
