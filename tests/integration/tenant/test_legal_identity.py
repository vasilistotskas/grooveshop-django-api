"""A storefront must publish who is selling.

The seller's identity already existed as ``INVOICE_SELLER_*`` settings,
used only when rendering an invoice. But the same facts carry a second,
independent obligation: they must be published on the site itself.

- e-Commerce Directive 2000/31/EC art. 5(1): name (a), geographic
  address of establishment (b), contact details allowing rapid and
  direct communication (c), trade register + registration number (d),
  VAT identification number (g) — "easily, directly and permanently
  accessible".
- N. 4919/2022 art. 22 §3: the GEMI number on the e-shop.
- N. 4919/2022 art. 22 §4: legal form, company name, registered seat and
  liquidation status "σε εμφανές σημείο". €200-500 fine under art. 50(γ).

Two of those — legal form and liquidation status — had no field at all,
so no merchant could have been compliant. These tests pin the endpoint,
the shared definition, and the fact that blanks stay visible.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from extra_settings.models import Setting

from tenant.legal_identity import (
    IN_LIQUIDATION_KEY,
    REQUIRED_DISCLOSURE_FIELDS,
    SELLER_SETTING_KEYS,
    is_disclosure_complete,
    merchant_legal_identity,
    missing_disclosure_fields,
)


def _set(key: str, value, type_="string"):
    """Write a setting and confirm it reads back through the same
    accessor the endpoint uses.

    The read-back is not ceremony. These tests failed once in a full
    parallel run with the endpoint publishing the seeded blank instead of
    the value written here, and the cause was never established — the
    settings cache is inert under the test cache config, each xdist
    worker has its own database, and nothing re-seeds during a request.
    Asserting at the write turns a recurrence into "the row did not
    land", pointing at the writer, instead of an opaque empty string at
    the assertion twenty lines later.
    """
    Setting.objects.update_or_create(
        name=key,
        defaults={
            "value_string" if type_ == "string" else "value_bool": value,
            "value_type": type_,
        },
    )
    readback = Setting.get(key, default=None)
    assert readback == value, (
        f"{key} was written as {value!r} but reads back as {readback!r}"
    )


def _state(key: str) -> str:
    """What the DB, the accessor and the connection say, right now.

    These tests have failed in a full parallel run with the endpoint
    publishing the seeded blank instead of the value `_set` wrote and
    read back — and the mechanism was never established. The settings
    cache is inert under the test cache config, each xdist worker has
    its own database, and nothing re-seeds during a request, so the
    obvious explanations are ruled out.

    Rather than guess again, a failure now reports the three things that
    separate the remaining candidates: the row as stored, what the
    accessor returns, and which schema the connection is pointed at (a
    test that assigns `connection.tenant` directly instead of calling
    `set_tenant()` strands `schema_name`, and later reads then land in a
    different schema's table).
    """
    from django.db import connection

    row = (
        Setting.objects.filter(name=key)
        .values_list("value_string", "value_bool", "value_type")
        .first()
    )
    tenant = getattr(connection, "tenant", None)
    return (
        f"{key}: row={row!r} "
        f"accessor={Setting.get(key, default=None)!r} "
        f"schema={getattr(connection, 'schema_name', '?')!r} "
        f"tenant={getattr(tenant, 'schema_name', None)!r}"
    )


@pytest.mark.django_db
class TestOneDefinition:
    """Invoice and storefront must never name a different seller."""

    def test_invoicing_imports_the_shared_map(self):
        from order.invoicing import INVOICE_SELLER_SETTING_KEYS

        assert INVOICE_SELLER_SETTING_KEYS is SELLER_SETTING_KEYS, (
            "invoicing kept its own copy — the two will drift and an "
            "invoice will name a different seller than the site does"
        )

    def test_the_two_previously_missing_fields_exist(self):
        """Neither had a field, so no merchant could comply with §4."""
        assert "legal_form" in SELLER_SETTING_KEYS
        assert IN_LIQUIDATION_KEY == "INVOICE_SELLER_IN_LIQUIDATION"

    def test_required_set_is_what_the_law_names(self):
        # tax_office / business_activity are invoice concerns, not
        # disclosure ones — publishing more than required is the
        # merchant's decision, not a platform default.
        assert set(REQUIRED_DISCLOSURE_FIELDS) == {
            "name",
            "legal_form",
            "registration_number",
            "vat_id",
            "address_line_1",
            "city",
            "postal_code",
            "country",
            "email",
        }


@pytest.mark.django_db
class TestBlanksStayVisible:
    """An unconfigured merchant is non-compliant and must look it."""

    def test_unset_identity_reports_every_required_field_missing(self):
        missing = missing_disclosure_fields()
        assert set(missing) == set(REQUIRED_DISCLOSURE_FIELDS)
        assert not is_disclosure_complete()

    def test_no_plausible_fallback_is_invented(self):
        """Invoicing falls back to the site name; disclosure must not.

        A fabricated legal name published as the seller's identity is
        worse than a visible blank — it looks compliant and is wrong.
        """
        identity = merchant_legal_identity()
        assert identity["name"] == ""
        assert identity["vat_id"] == ""

    def test_completing_every_required_field_clears_the_gap(self):
        for field in REQUIRED_DISCLOSURE_FIELDS:
            _set(SELLER_SETTING_KEYS[field], f"value-{field}")
        assert missing_disclosure_fields() == []
        assert is_disclosure_complete()

    def test_whitespace_does_not_count_as_provided(self):
        for field in REQUIRED_DISCLOSURE_FIELDS:
            _set(SELLER_SETTING_KEYS[field], "   ")
        assert set(missing_disclosure_fields()) == set(
            REQUIRED_DISCLOSURE_FIELDS
        )


@pytest.mark.django_db
class TestLiquidationFlag:
    def test_defaults_to_false(self):
        assert merchant_legal_identity()["in_liquidation"] is False

    def test_reads_as_a_real_boolean_not_a_string(self):
        """Folding it into the string map would yield 'False', truthy."""
        _set(IN_LIQUIDATION_KEY, True, type_="bool")
        value = merchant_legal_identity()["in_liquidation"]
        assert value is True
        assert not isinstance(value, str)


@pytest.mark.django_db
class TestEndpoint:
    def test_is_public(self, client):
        """Every field is information the merchant must publish, so
        there is nothing here to gate behind auth."""
        response = client.get(reverse("tenant:tenant-legal-identity"))
        assert response.status_code == 200

    def test_returns_every_identity_field(self, client):
        """Bodies are camelCased by djangorestframework-camel-case."""

        def camel(name: str) -> str:
            head, *rest = name.split("_")
            return head + "".join(part.title() for part in rest)

        response = client.get(reverse("tenant:tenant-legal-identity"))
        body = response.json()
        for field in SELLER_SETTING_KEYS:
            assert camel(field) in body, f"{field} missing from the payload"
        assert "inLiquidation" in body

    def test_reports_its_own_incompleteness(self, client):
        """The storefront needs to know the disclosure is not compliant
        rather than rendering a half-empty block that looks intentional."""
        response = client.get(reverse("tenant:tenant-legal-identity"))
        body = response.json()
        complete = body.get("isComplete", body.get("is_complete"))
        missing = body.get("missingFields", body.get("missing_fields"))
        assert complete is False
        assert set(missing) == set(REQUIRED_DISCLOSURE_FIELDS)

    def test_publishes_what_the_merchant_set(self, client):
        _set("INVOICE_SELLER_NAME", "Acme MON IKE")
        _set("INVOICE_SELLER_LEGAL_FORM", "ΙΚΕ")
        _set("INVOICE_SELLER_REGISTRATION_NUMBER", "123456789000")
        _set("INVOICE_SELLER_VAT_ID", "EL999999999")

        body = client.get(reverse("tenant:tenant-legal-identity")).json()

        assert body["name"] == "Acme MON IKE", _state("INVOICE_SELLER_NAME")
        assert body.get("legalForm", body.get("legal_form")) == "ΙΚΕ"
        assert (
            body.get("registrationNumber", body.get("registration_number"))
            == "123456789000"
        )
        assert body.get("vatId", body.get("vat_id")) == "EL999999999"
