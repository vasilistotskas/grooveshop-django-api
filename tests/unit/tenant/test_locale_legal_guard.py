"""Enabling a locale is refused while its legal documents are missing.

``Tenant.clean`` guards the TRANSITION, not the state: only locales this
save adds are checked. A tenant whose stored set is already inconsistent
— delta-sigma serves ``en`` with Greek-only legal documents — must stay
editable for every unrelated field, or the guard would lock operators
out of rows it was meant to protect.

The coverage rule itself is tested in
``tests/unit/page_config/test_legal_translation_coverage.py``; what is
asserted here is the wiring: which locales get checked, and when the
check is skipped.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from page_config.legal_documents import LEGAL_DOCUMENT_SLUGS
from tenant.models import Tenant

GREEK_ONLY = {slug: {"el"} for slug in LEGAL_DOCUMENT_SLUGS}
BOTH = {slug: {"el", "en"} for slug in LEGAL_DOCUMENT_SLUGS}


@pytest.fixture
def tenant(db) -> Tenant:
    # `auto_create_schema = False`: creating a schema per test is ~50x
    # slower and this guard never reads one (the coverage reader is
    # patched, and `schema_exists` is what decides whether it runs).
    instance = Tenant(
        schema_name="guardtest",
        name="Guard Test",
        default_locale="el",
        available_locales=["el"],
    )
    instance.auto_create_schema = False
    instance.save()
    return instance


def _run_clean(tenant: Tenant, coverage: dict[str, set[str]]) -> None:
    with (
        mock.patch("django_tenants.utils.schema_exists", return_value=True),
        mock.patch("django_tenants.utils.tenant_context"),
        mock.patch(
            "page_config.defaults.legal_translation_coverage",
            return_value=coverage,
        ),
    ):
        tenant._validate_legal_documents_for_new_locales()


class TestNewlyAddedLocales:
    def test_reports_only_what_this_save_adds(self, tenant: Tenant) -> None:
        tenant.available_locales = ["el", "en"]

        assert tenant._newly_added_locales() == ["en"]

    def test_an_unchanged_set_adds_nothing(self, tenant: Tenant) -> None:
        assert tenant._newly_added_locales() == []

    def test_removing_a_locale_adds_nothing(self, tenant: Tenant) -> None:
        tenant.available_locales = []

        assert tenant._newly_added_locales() == []

    def test_an_unsaved_tenant_has_no_previous_set(self) -> None:
        # Provisioning seeds the documents AFTER the row exists, so
        # there is nothing to validate against yet.
        fresh = Tenant(schema_name="fresh", name="Fresh", default_locale="el")
        fresh.available_locales = ["el", "en"]

        assert fresh._newly_added_locales() == []


class TestLegalDocumentGuard:
    def test_refuses_a_locale_with_no_translated_documents(
        self, tenant: Tenant
    ) -> None:
        tenant.available_locales = ["el", "en"]

        with pytest.raises(ValidationError) as excinfo:
            _run_clean(tenant, GREEK_ONLY)

        message = str(excinfo.value)
        assert "available_locales" in excinfo.value.message_dict
        for slug in LEGAL_DOCUMENT_SLUGS:
            assert slug in message
        assert "en" in message

    def test_allows_a_locale_whose_documents_are_translated(
        self, tenant: Tenant
    ) -> None:
        tenant.available_locales = ["el", "en"]

        _run_clean(tenant, BOTH)

    def test_names_only_the_untranslated_document(self, tenant: Tenant) -> None:
        coverage = {**BOTH, "privacy": {"el"}}
        tenant.available_locales = ["el", "en"]

        with pytest.raises(ValidationError) as excinfo:
            _run_clean(tenant, coverage)

        assert "privacy" in str(excinfo.value)
        assert "terms" not in str(excinfo.value)

    def test_leaves_an_already_inconsistent_tenant_editable(
        self, tenant: Tenant
    ) -> None:
        # delta-sigma's state: `en` already stored, documents Greek-only.
        # Saving an unrelated field must not be blocked by history.
        tenant.available_locales = ["el", "en"]
        tenant.save()
        tenant.name = "Renamed"

        _run_clean(tenant, GREEK_ONLY)

    def test_skips_when_the_schema_does_not_exist_yet(
        self, tenant: Tenant
    ) -> None:
        tenant.available_locales = ["el", "en"]

        with (
            mock.patch(
                "django_tenants.utils.schema_exists", return_value=False
            ),
            mock.patch(
                "page_config.defaults.legal_translation_coverage"
            ) as reader,
        ):
            tenant._validate_legal_documents_for_new_locales()

        reader.assert_not_called()

    def test_clean_runs_the_guard(self, tenant: Tenant) -> None:
        # Without this, deleting the call from `clean()` breaks nothing:
        # every other test here drives the private method directly, and
        # the admin only ever goes through `clean()`.
        tenant.available_locales = ["el", "en"]

        with (
            mock.patch("django_tenants.utils.schema_exists", return_value=True),
            mock.patch("django_tenants.utils.tenant_context"),
            mock.patch(
                "page_config.defaults.legal_translation_coverage",
                return_value=GREEK_ONLY,
            ),
            pytest.raises(ValidationError) as excinfo,
        ):
            tenant.clean()

        assert "available_locales" in excinfo.value.message_dict

    def test_does_not_read_the_schema_when_nothing_was_added(
        self, tenant: Tenant
    ) -> None:
        with mock.patch(
            "page_config.defaults.legal_translation_coverage"
        ) as reader:
            tenant._validate_legal_documents_for_new_locales()

        reader.assert_not_called()
