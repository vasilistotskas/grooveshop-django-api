"""AADE enum constants used in myDATA payloads.

Kept as module-level constants (not ``IntEnum``) because AADE ships
these as plain integer / string codes in the XSD — using Python
enums would add serialisation friction with no type-safety upside.

Source: AADE myDATA API Documentation v1.0.12.
"""

from __future__ import annotations

# ── invoiceType: the document-kind enum ──────────────────────────
#
# The platform issues retail documents only, over the ERP ``SendInvoices``
# channel: the retail receipt (11.1, ΕΛΠ art. 12(1)) and the retail credit
# document (11.4, art. 12(4)). Invoices (1.1) and credit invoices (5.1)
# are NOT issued here: from 2.11.2026 AADE decision Α.1128/2025 art. 1(2)
# requires them to come from a licensed provider or AADE's timologio app,
# and an ERP-issued one counts as not issued.
INVOICE_TYPE_RETAIL_RECEIPT = "11.1"  # Α.Λ.Π. — retail sales receipt
INVOICE_TYPE_RETAIL_CREDIT = "11.4"  # Πιστωτικό Στοιχείο Λιανικής

# ── paymentMethodDetails.type ────────────────────────────────────
#
# Strictly per AADE v1.0.10 annex 8.12 (PDF lines 5507–5511). Ordering
# here MUST match the spec numbering — an earlier version of this
# module had every code shifted by one, which silently sent
# "Μετρητά" (cash) for every card payment and "POS" for every COD
# order. The spec table is the only source of truth.
PAYMENT_METHOD_CASH = 3  # Μετρητά — cash / cash on delivery
PAYMENT_METHOD_WEB_BANKING = 6  # Web Banking
PAYMENT_METHOD_POS_CARD = 7  # POS / e-POS — card online & terminals

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
VAT_CATEGORY_3 = 9  # 3 % (law 5057/2023)


# ── vatExemptionCategory ─────────────────────────────────────────
# Required alongside ``vatCategory`` when the rate is 0 (AADE error
# 217 otherwise). We default retail 0% lines to VAT-free general
# category (no article 22-28 specifics). Extend as needed when new
# exemption paths come online.
VAT_EXEMPTION_NO_VAT_ARTICLES = 30  # Λοιπές εξαιρέσεις ΦΠΑ


# ── Classification types for invoiceDetails + invoiceSummary ─────
# Every document MUST carry an income classification (AADE error 314
# otherwise). Goods are ``category1_1`` ("Έσοδα από Πώληση Εμπορευμάτων");
# ``category1_3`` is "Έσοδα από Παροχή Υπηρεσιών" and is wrong for a
# webshop's goods. AADE's classification combinations (v2.0.2, sheets
# 11.1 and 11.4) allow ``category1_1`` with ``E3_561_003`` ("Πωλήσεις
# Λιανικές — Ιδιωτική Πελατεία"), for the receipt and its credit alike.
CLASSIFICATION_TYPE_RETAIL_GOODS = "E3_561_003"
CLASSIFICATION_CATEGORY_GOODS_SALES = "category1_1"


# ── AADE error codes we branch on explicitly ─────────────────────
# Full catalogue lives in the AADE portal; we only name-check the
# ones with custom handling. Everything else is treated as a
# terminal ``ValidationError``.
ERROR_DUPLICATE_UID = "228"  # uid already registered under another MARK
