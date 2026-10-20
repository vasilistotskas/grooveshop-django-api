"""German is no longer a language of the platform: its rows are deleted.

The ISO seed (``0012_seed_iso_countries``) wrote a German name for every
country, so a fresh database and production both hold them until
``0014_remove_german_translations`` runs.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps

from country.models import Country

MIGRATION = "country.migrations.0014_remove_german_translations"


@pytest.mark.django_db
def test_german_rows_go_and_the_other_languages_stay():
    country = Country.objects.get(alpha_2="CY")
    translation = Country.translations.rel.related_model
    translation.objects.create(
        master=country, language_code="de", name="Zypern"
    )
    kept = translation.objects.filter(master=country).exclude(
        language_code="de"
    )
    before = kept.count()
    assert before > 0

    migration = importlib.import_module(MIGRATION)
    forward = migration.Migration.operations[0].code
    forward(apps, None)

    assert not translation.objects.filter(language_code="de").exists()
    assert kept.count() == before
