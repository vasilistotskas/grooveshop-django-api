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
#
# Strictly per AADE v1.0.10 annex 8.12 (PDF lines 5507–5511). Ordering
# here MUST match the spec numbering — an earlier version of this
# module had every code shifted by one, which silently sent
# "Μετρητά" (cash) for every card payment and "POS" for every COD
# order. The spec table is the only source of truth.
PAYMENT_METHOD_DOMESTIC_BANK = 1  # Επαγ. Λογ. Πληρωμών Ημεδαπής
PAYMENT_METHOD_FOREIGN_BANK = 2  # Επαγ. Λογ. Πληρωμών Αλλοδαπής
PAYMENT_METHOD_CASH = 3  # Μετρητά — cash / cash on delivery
PAYMENT_METHOD_CHEQUE = 4  # Επιταγή
PAYMENT_METHOD_ON_CREDIT = 5  # Επί Πιστώσει
PAYMENT_METHOD_WEB_BANKING = 6  # Web Banking
PAYMENT_METHOD_POS_CARD = 7  # POS / e-POS — card online & terminals
PAYMENT_METHOD_IRIS = 8  # Άμεσες Πληρωμές IRIS (SEPA Instant)

# ── vatCategory: AADE code for each rate band ────────────────────
# Standard mainland rates 1–3; island-discount rates 4–6; zero /
# exempt / reverse-charge 7–8; 2023 law 5057 additions 9–10.
VAT_CATEGORY_24 = 1  # 24 %
VAT_CATEGORY_13 = 2  # 13 %
VAT_CATEGORY_6 = 3  # 6 %
VAT_CATEGORY_17 = 4  # 17 % (island discount)
VAT_CATEGORY_9 = 5  # 9 % (island discount)
VAT_CATEGORY_4 = 6  # 4 % (island discount)
VAT_CATEGORY_0 = 7  # 0 %
VAT_CATEGORY_EXEMPT = 8  # Exempt (requires vatExemptionCategory)
VAT_CATEGORY_3 = 9  # 3 % (law 5057/2023)
VAT_CATEGORY_4_NEW = 10  # 4 % (law 5057/2023)


# ── vatExemptionCategory ─────────────────────────────────────────
# Required alongside ``vatCategory`` when the rate is 0 (AADE error
# 217 otherwise). We default retail 0% lines to VAT-free general
# category (no article 22-28 specifics). Extend as needed when new
# exemption paths come online.
VAT_EXEMPTION_NO_VAT_ARTICLES = 30  # Λοιπές εξαιρέσεις ΦΠΑ


# ── Classification types for invoiceDetails + invoiceSummary ─────
# Every invoice MUST carry either an income or expenses classification
# (AADE error 314 otherwise). The valid ``type``/``category`` pair is
# specific to the invoiceType:
#   * 11.1 (Α.Λ.Π. / retail receipt — Tier A target):
#     ``E3_561_003`` (Πωλήσεις Λιανικές — Ιδιωτική Πελατεία) paired
#     with ``category1_3`` (same label). Verified via AADE dev: the
#     ``E3_561_001`` / ``category1_1`` pair triggers error 313
#     because ``category1_1`` is Χονδρικές (wholesale / B2B).
#   * 1.1 (B2B sales invoice — Tier B):
#     ``E3_561_001`` + ``category1_1`` (wholesale).
#   * 5.x / 11.4 credit notes: not implemented yet.
CLASSIFICATION_TYPE_RETAIL_GOODS = "E3_561_003"
CLASSIFICATION_CATEGORY_GOODS_SALES = "category1_3"


# ── AADE error codes we branch on explicitly ─────────────────────
# Full catalogue lives in the AADE portal; we only name-check the
# ones with custom handling. Everything else is treated as a
# terminal ``ValidationError``.
ERROR_DUPLICATE_UID = "228"  # uid already registered under another MARK
ERROR_INACTIVE_VAT = "102"  # counterpart VAT is not on AADE's registry
ERROR_XML_SYNTAX = "101"
ERROR_WRONG_VAT_CATEGORY = "216"
ERROR_MISSING_VAT_EXEMPTION = "217"
ERROR_MISSING_CLASSIFICATION = "314"  # income or expenses required
