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
#: than is required is the merchant's call, not a default.
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


def merchant_legal_identity() -> dict[str, object]:
    """The current tenant's published legal identity.

    Every value is returned as stored, including blanks. Blanks are
    meaningful: a merchant who has not filled these in is not compliant,
    and the caller needs to be able to SEE that rather than receive a
    plausible-looking fallback. ``order.invoicing`` deliberately does
    fall back to the site name for its own rendering; that is an invoice
    concern and does not belong in the published identity.
    """
    from extra_settings.models import Setting

    identity: dict[str, object] = {
        field: str(Setting.get(key, default="") or "").strip()
        for field, key in SELLER_SETTING_KEYS.items()
    }
    identity["in_liquidation"] = bool(
        Setting.get(IN_LIQUIDATION_KEY, default=False)
    )
    return identity


def missing_disclosure_fields() -> list[str]:
    """Which legally required fields the merchant has left blank.

    Lets the admin and the onboarding flow surface the gap before it
    becomes a fine, instead of discovering it from a complaint.
    """
    identity = merchant_legal_identity()
    return [
        field
        for field in REQUIRED_DISCLOSURE_FIELDS
        if not str(identity.get(field, "")).strip()
    ]


def is_disclosure_complete() -> bool:
    return not missing_disclosure_fields()
