"""AADE enum constants used in myDATA payloads.

Kept as module-level constants (not ``IntEnum``) because AADE ships
these as plain integer / string codes in the XSD — using Python
enums would add serialisation friction with no type-safety upside.

Source: AADE myDATA API Documentation v1.0.12.
"""

from __future__ import annotations

# ── invoiceType: the document-kind enum ──────────────────────────
#
# This is a narrow whitelist for Tier A (B2C retail only). The full
# catalogue (1.1 B2B sales, 2.x services, 5.x credit notes, 11.x
# retail, 13.x expenses, 17.x depreciation) lives in the AADE docs;
# add entries here as new document types are wired up.
INVOICE_TYPE_B2C_RETAIL = "11.1"  # Α.Λ.Π. — retail sales receipt
INVOICE_TYPE_B2B_SALES = "1.1"  # Τιμολόγιο Πώλησης — full invoice
INVOICE_TYPE_CREDIT_LINKED = "5.1"  # Linked credit note
INVOICE_TYPE_RETAIL_CREDIT = "11.4"  # Retail return / credit element

# ── paymentMethodDetails.type ────────────────────────────────────
PAYMENT_METHOD_DOMESTIC_BANK = 1  # Domestic payment account
PAYMENT_METHOD_FOREIGN_BANK = 2  # Foreign payment account
PAYMENT_METHOD_WEB_BANKING = 3  # Cash / card online / web-banking
PAYMENT_METHOD_POS_CARD = 4  # POS / card terminal
PAYMENT_METHOD_OTHER = 5  # Other
PAYMENT_METHOD_ON_CREDIT = 6  # Credit
PAYMENT_METHOD_CASH = 7  # Cash / cash on delivery

# ── vatCategory: AADE code for each rate band ────────────────────
VAT_CATEGORY_24 = 1  # 24 %
VAT_CATEGORY_13 = 2  # 13 %
VAT_CATEGORY_6 = 3  # 6 %
VAT_CATEGORY_17 = 4  # 17 % (island discount)
VAT_CATEGORY_9 = 5  # 9 % (island discount)
VAT_CATEGORY_4 = 6  # 4 % (island discount)
VAT_CATEGORY_0 = 7  # 0 %
VAT_CATEGORY_EXEMPT = 8  # Exempt (requires vatExemptionCategory)


# ── AADE error codes we branch on explicitly ─────────────────────
# Full catalogue lives in the AADE portal; we only name-check the
# ones with custom handling. Everything else is treated as a
# terminal ``ValidationError``.
ERROR_DUPLICATE_UID = "228"  # uid already registered under another MARK
ERROR_INACTIVE_VAT = "102"  # counterpart VAT is not on AADE's registry
ERROR_XML_SYNTAX = "101"
ERROR_WRONG_VAT_CATEGORY = "216"
ERROR_MISSING_VAT_EXEMPTION = "217"
