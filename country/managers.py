from django.db import models
from parler.managers import TranslatableManager, TranslatableQuerySet


class CountryQuerySet(TranslatableQuerySet):
    def with_regions(self):
        return self.prefetch_related("regions")

    def active_countries(self):
        return self.filter(iso_cc__isnull=False)

    def by_continent(self, continent):
        return self

    def with_phone_code(self):
        return self.filter(phone_code__isnull=False)

    def search_by_name(self, query):
        return self.filter(translations__name__icontains=query)

    def search_by_code(self, query):
        query_upper = query.upper()
        return self.filter(
            models.Q(alpha_2__icontains=query_upper)
            | models.Q(alpha_3__icontains=query_upper)
        )


class CountryManager(TranslatableManager):
    def get_queryset(self):
        return CountryQuerySet(self.model, using=self._db)

    def with_regions(self):
        return self.get_queryset().with_regions()

    def active_countries(self):
        return self.get_queryset().active_countries()

    def by_continent(self, continent):
        return self.get_queryset().by_continent(continent)

    def with_phone_code(self):
        return self.get_queryset().with_phone_code()

    def search_by_name(self, query):
        return self.get_queryset().search_by_name(query)

    def search_by_code(self, query):
        return self.get_queryset().search_by_code(query)
