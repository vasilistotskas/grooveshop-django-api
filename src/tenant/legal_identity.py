"""The merchant's legal identity — who is actually selling.

This is one set of facts serving two obligations that are easy to treat
as unrelated:

1. **Invoices.** Greek tax law requires the seller's ΑΦΜ, ΔΟΥ, ΓΕΜΗ and
   registered address on every issued invoice (``order.invoicing``).
2. **The storefront.** The identity must ALSO be published on the site
   itself, which is a separate obligation with a separate legal basis:

   - e-Commerce Directive 2000/31/EC art. 5(1) — the name (a), the
     geographic address of establishment (b), contact details allowing
     rapid and direct communication (c), the trade register and
     registration number (d), and the VAT identification number (g),
     all "easily, directly and permanently accessible".
   - N. 4919/2022 art. 22 §3 — the ΓΕΜΗ number on the e-shop.
   - N. 4919/2022 art. 22 §4 — the legal form, company name, registered
     seat and, where it applies, that the company is in liquidation,
     "σε εμφανές σημείο" (in a prominent place). Fine for omission is
     €200-500 under art. 50(γ).

The data already existed for (1) as ``INVOICE_SELLER_*`` settings and
was simply never published for (2). Keeping ONE map here — rather than
adding a parallel set of merchant-identity fields on ``Tenant`` — means
the merchant fills this in once and the invoice and the storefront can
never disagree about who the seller is. Two sources would drift, and a
disagreement between an invoice and the published identity is exactly
the kind of discrepancy that draws a complaint.

The ``INVOICE_SELLER_`` key prefix is now narrower than the use, but the
keys hold live per-tenant data and renaming them buys nothing a comment
does not.

Values live in ``extra_settings``, which is TENANT-schema scoped, so
every read here resolves for the tenant the request is running in.
Callers must therefore already be in tenant context — the public schema
has no store to describe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Field name on the returned identity -> ``extra_settings`` key.
#: ``order.invoicing`` imports this: invoicing is a CONSUMER of the
#: merchant's identity, so the definition belongs here and the
#: dependency points this way, not the reverse.
SELLER_SETTING_KEYS: Mapping[str, str] = {
    "name": "INVOICE_SELLER_NAME",
    "distinctive_title": "INVOICE_SELLER_DISTINCTIVE_TITLE",
    "legal_form": "INVOICE_SELLER_LEGAL_FORM",
    "vat_id": "INVOICE_SELLER_VAT_ID",
    "tax_office": "INVOICE_SELLER_TAX_OFFICE",
    "registration_number": "INVOICE_SELLER_REGISTRATION_NUMBER",
    "business_activity": "INVOICE_SELLER_BUSINESS_ACTIVITY",
    "address_line_1": "INVOICE_SELLER_ADDRESS_LINE_1",
    "address_line_2": "INVOICE_SELLER_ADDRESS_LINE_2",
    "city": "INVOICE_SELLER_CITY",
    "postal_code": "INVOICE_SELLER_POSTAL_CODE",
    "country": "INVOICE_SELLER_COUNTRY",
    "phone": "INVOICE_SELLER_PHONE",
    "email": "INVOICE_SELLER_EMAIL",
}

#: Booleans are read separately — ``Setting.get`` returns them typed,
#: and folding them into the string map above would stringify them.
IN_LIQUIDATION_KEY = "INVOICE_SELLER_IN_LIQUIDATION"

#: What art. 22 §4 and ECD art. 5 actually require to be published.
#: ``tax_office`` and ``business_activity`` are invoice concerns, not
#: disclosure ones, so they are deliberately absent — publishing more
#: than is required is the merchant's call, not a default. So is
#: ``distinctive_title``: it never replaces the legal ``name``, which stays
#: required, and a blank one is not a gap.
REQUIRED_DISCLOSURE_FIELDS: tuple[str, ...] = (
    "name",
    "legal_form",
    "registration_number",
    "vat_id",
    "address_line_1",
    "city",
    "postal_code",
    "country",
    "email",
)


#: What a tax document needs about the seller. Greek law puts the ΑΦΜ and
#: ΔΟΥ on every document the seller issues, and myDATA refuses to take a
#: submission without the ΑΦΜ (``order.mydata.service``). Unlike
#: ``REQUIRED_DISCLOSURE_FIELDS`` this only matters to a store that
#: actually issues tax documents (``MYDATA_ENABLED``).
REQUIRED_TAX_DOCUMENT_FIELDS: tuple[str, ...] = (
    "name",
    "vat_id",
    "tax_office",
)


def merchant_legal_identity() -> dict[str, object]:
    """The current tenant's published legal identity.

    Every value is returned as stored, including blanks. Blanks are
    meaningful: a merchant who has not filled these in is not compliant,
    and the caller needs to be able to SEE that rather than receive a
    plausible-looking fallback.
    """
    from core.store_settings import store_settings

    values = store_settings((*SELLER_SETTING_KEYS.values(), IN_LIQUIDATION_KEY))
    identity: dict[str, object] = {
        field: str(values[key]).strip()
        for field, key in SELLER_SETTING_KEYS.items()
    }
    identity["in_liquidation"] = bool(values[IN_LIQUIDATION_KEY])
    return identity


def _blank(
    fields: tuple[str, ...], identity: Mapping[str, object] | None
) -> list[str]:
    identity = merchant_legal_identity() if identity is None else identity
    return [f for f in fields if not str(identity.get(f, "")).strip()]


def missing_disclosure_fields(
    identity: Mapping[str, object] | None = None,
) -> list[str]:
    """Which legally required fields the merchant has left blank.

    Lets the admin and the onboarding flow surface the gap before it
    becomes a fine, instead of discovering it from a complaint. A caller
    that checks several field sets passes the ``identity`` it already
    read, so the settings are fetched once.
    """
    return _blank(REQUIRED_DISCLOSURE_FIELDS, identity)


def missing_tax_document_fields(
    identity: Mapping[str, object] | None = None,
) -> list[str]:
    """Which fields a tax document needs that are still blank."""
    return _blank(REQUIRED_TAX_DOCUMENT_FIELDS, identity)
