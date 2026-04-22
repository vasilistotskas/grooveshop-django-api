"""Unit tests for ``order.invoicing``.

Covers the parts that don't need WeasyPrint's GTK stack: the atomic
counter, the VAT breakdown computation, the totals derivation, and
idempotency of ``generate_invoice``. The actual PDF render step
(``_render_pdf_bytes``) is mocked — we care about the data the template
sees, not byte-for-byte pixel-matching a PDF (which would be brittle
across WeasyPrint versions anyway).
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase, override_settings
from djmoney.money import Money

from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.invoicing import (
    _build_context,
    _build_qr_svg,
    _compute_vat_breakdown,
    _order_totals,
    _render_items,
    generate_invoice,
)
from order.models.invoice import Invoice, InvoiceCounter
from product.factories.product import ProductFactory
from vat.factories import VatFactory


class InvoiceCounterAtomicTestCase(TransactionTestCase):
    """The counter is the only source of truth for invoice numbers —
    Greek tax law forbids gaps. Verify concurrent allocation hands out
    distinct numbers even from different threads."""

    def test_allocate_creates_counter_on_first_call(self) -> None:
        self.assertFalse(InvoiceCounter.objects.filter(year=2026).exists())
        number = InvoiceCounter.allocate(2026)
        self.assertEqual(number, "INV-2026-000001")
        counter = InvoiceCounter.objects.get(year=2026)
        self.assertEqual(counter.next_number, 2)

    def test_sequential_allocation(self) -> None:
        numbers = [InvoiceCounter.allocate(2026) for _ in range(5)]
        self.assertEqual(
            numbers,
            [
                "INV-2026-000001",
                "INV-2026-000002",
                "INV-2026-000003",
                "INV-2026-000004",
                "INV-2026-000005",
            ],
        )

    def test_per_year_counters_are_independent(self) -> None:
        self.assertEqual(InvoiceCounter.allocate(2025), "INV-2025-000001")
        self.assertEqual(InvoiceCounter.allocate(2026), "INV-2026-000001")
        self.assertEqual(InvoiceCounter.allocate(2025), "INV-2025-000002")

    def test_concurrent_allocation_never_duplicates(self) -> None:
        # Spawn a handful of threads racing on the counter row. With
        # select_for_update holding the row lock, each allocation must
        # see a distinct number — no gaps and no duplicates.
        N = 10
        results: list[str] = []
        errors: list[BaseException] = []

        def worker() -> None:
            from django.db import connection

            try:
                results.append(InvoiceCounter.allocate(2026))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertFalse(errors, f"Thread errors: {errors}")
        self.assertEqual(len(results), N)
        self.assertEqual(len(set(results)), N, "Duplicate numbers issued")
        # Numbers should be 1..N in some order (no gaps allowed).
        extracted = sorted(int(n.split("-")[-1]) for n in results)
        self.assertEqual(extracted, list(range(1, N + 1)))


class VatBreakdownTestCase(TestCase):
    def _make_item(self, order, product, *, amount: str, quantity: int):
        """Create an OrderItem with an exact price/quantity.

        Bypasses ``OrderItemFactory`` because its ``django_get_or_create``
        on (order, product) reuses existing rows from fixture pollution
        and its ``LazyFunction`` prices are random; neither is friendly
        to arithmetic assertions. We create the row explicitly.
        """
        from order.models.item import OrderItem

        return OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal(amount), "EUR"),
            quantity=quantity,
            original_quantity=quantity,
        )

    def test_single_rate_aggregates(self) -> None:
        vat_24 = VatFactory(value=24)
        product = ProductFactory(vat=vat_24)
        order = OrderFactory(num_order_items=0)
        self._make_item(order, product, amount="12.40", quantity=2)

        breakdown = _compute_vat_breakdown(order)
        self.assertEqual(len(breakdown), 1)
        row = breakdown[0]
        self.assertEqual(Decimal(row["rate"]), Decimal("24"))
        # 12.40 gross × 2 includes 24% VAT → subtotal = 24.80 / 1.24 = 20.00
        self.assertEqual(row["subtotal"], "20.00")
        self.assertEqual(row["vat"], "4.80")
        self.assertEqual(row["gross"], "24.80")

    def test_mixed_rates_keep_separate_buckets(self) -> None:
        vat_24 = VatFactory(value=24)
        vat_6 = VatFactory(value=6)
        p_standard = ProductFactory(vat=vat_24)
        p_reduced = ProductFactory(vat=vat_6)
        order = OrderFactory(num_order_items=0)
        self._make_item(order, p_standard, amount="12.40", quantity=1)
        self._make_item(order, p_reduced, amount="10.60", quantity=1)

        breakdown = _compute_vat_breakdown(order)
        rates = [Decimal(row["rate"]) for row in breakdown]
        # Sorted descending by rate (Greek convention).
        self.assertEqual(rates, [Decimal("24"), Decimal("6")])
        self.assertEqual(breakdown[0]["vat"], "2.40")
        self.assertEqual(breakdown[1]["vat"], "0.60")

    def test_no_vat_bucketed_under_zero_rate(self) -> None:
        product = ProductFactory(vat=None)
        order = OrderFactory(num_order_items=0)
        self._make_item(order, product, amount="5.00", quantity=1)

        breakdown = _compute_vat_breakdown(order)
        self.assertEqual(len(breakdown), 1)
        self.assertEqual(Decimal(breakdown[0]["rate"]), Decimal("0"))
        self.assertEqual(breakdown[0]["vat"], "0.00")
        self.assertEqual(breakdown[0]["subtotal"], "5.00")


class OrderTotalsTestCase(TestCase):
    def test_adds_shipping_and_payment_fee_on_top(self) -> None:
        order = OrderFactory(num_order_items=0)
        order.shipping_price = Money(Decimal("3.50"), "EUR")
        order.payment_method_fee = Money(Decimal("1.25"), "EUR")
        breakdown = [
            {
                "rate": "24",
                "subtotal": "10.00",
                "vat": "2.40",
                "gross": "12.40",
            },
        ]

        totals = _order_totals(order, breakdown)
        self.assertEqual(totals["subtotal"], Decimal("10.00"))
        self.assertEqual(totals["total_vat"], Decimal("2.40"))
        self.assertEqual(totals["shipping"], Decimal("3.50"))
        self.assertEqual(totals["payment_fee"], Decimal("1.25"))
        # 10 + 2.40 + 3.50 + 1.25 = 17.15
        self.assertEqual(totals["total"], Decimal("17.15"))


class GenerateInvoiceIdempotencyTestCase(TestCase):
    """``generate_invoice`` must be safe to call multiple times —
    ``handle_order_completed`` might re-fire on a status re-save and we
    rely on the idempotency guarantee rather than a separate flag."""

    def _make_order(self) -> object:
        vat = VatFactory(value=24)
        product = ProductFactory(vat=vat)
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=product,
            price=Money(Decimal("12.40"), "EUR"),
            quantity=1,
        )
        return order

    @patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 ... %EOF"
    )
    def test_second_call_returns_existing(self, _mock_render) -> None:
        order = self._make_order()
        first = generate_invoice(order)
        before = Invoice.objects.count()
        second = generate_invoice(order)
        self.assertEqual(Invoice.objects.count(), before)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.invoice_number, second.invoice_number)

    @patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 ... %EOF"
    )
    def test_force_refreshes_snapshots_without_new_row(
        self, _mock_render
    ) -> None:
        order = self._make_order()
        first = generate_invoice(order)
        # Simulate buyer edit
        order.first_name = "NewFirst"
        order.save()
        refreshed = generate_invoice(order, force=True)
        self.assertEqual(first.pk, refreshed.pk)
        self.assertIn("NewFirst", refreshed.buyer_snapshot.get("name", ""))

    @override_settings(DEFAULT_CURRENCY="EUR")
    @patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 ... %EOF"
    )
    def test_captures_seller_snapshot_and_vat_breakdown(
        self, _mock_render
    ) -> None:
        order = self._make_order()
        invoice = generate_invoice(order)
        self.assertTrue(invoice.invoice_number.startswith("INV-"))
        self.assertEqual(len(invoice.vat_breakdown), 1)
        self.assertEqual(
            Decimal(invoice.vat_breakdown[0]["rate"]), Decimal("24")
        )
        self.assertIn("name", invoice.seller_snapshot)
        self.assertIn("email", invoice.buyer_snapshot)
        self.assertEqual(invoice.currency, "EUR")


class RenderItemsTestCase(TestCase):
    """``_render_items`` resolves the product name via
    ``safe_translation_getter('name', ...)`` in Python — Django templates
    can't pass keyword arguments to methods, which is why the old
    template rendered the product ID instead of its name."""

    def test_resolves_product_name_and_sku(self) -> None:
        vat = VatFactory(value=24)
        product = ProductFactory(vat=vat)
        # ProductFactory seeds translations via parler; the 'el' row is
        # what gets picked up under ``translation_override('el')``.
        expected_name = (
            product.safe_translation_getter("name", any_language=True) or ""
        )
        self.assertNotEqual(expected_name, "", "Factory must seed a name")

        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=product,
            price=Money(Decimal("12.40"), "EUR"),
            quantity=2,
        )

        rows = _render_items(order)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], expected_name)
        self.assertEqual(row["sku"], product.sku)
        self.assertEqual(row["quantity"], 2)
        self.assertEqual(row["unit_gross"], Decimal("12.40"))
        self.assertEqual(row["vat_rate"], Decimal("24"))
        # 12.40 × 2 = 24.80
        self.assertEqual(row["line_total"], Decimal("24.80"))

    def test_product_without_vat_reports_none_rate(self) -> None:
        """None vs 0 matters — the template uses ``is not None`` to
        decide whether to render "X%" at all."""
        product = ProductFactory(vat=None)
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=product,
            price=Money(Decimal("5.00"), "EUR"),
            quantity=1,
        )

        rows = _render_items(order)
        self.assertIsNone(rows[0]["vat_rate"])


class QrSvgTestCase(TestCase):
    """The QR is rendered with ``SvgPathImage`` so it's a single ``<path>``
    element — WeasyPrint handles it as true vector geometry. Keeping
    the output tight matters because it's inlined into the PDF."""

    def test_returns_svg_string_containing_data(self) -> None:
        svg = _build_qr_svg("https://example.com/orders/1")
        self.assertIsInstance(svg, str)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        # SvgPathImage emits a single path element
        self.assertIn("<path", svg)


class BuildContextTestCase(TestCase):
    """``_build_context`` enriches the render context with fields the
    template needs (items, QR, pay-way display) while keeping the
    persisted ``vat_breakdown`` JSON unchanged."""

    @override_settings(NUXT_BASE_URL="https://shop.example.com")
    def test_context_exposes_qr_and_verification_url(self) -> None:
        vat = VatFactory(value=24)
        product = ProductFactory(vat=vat)
        order = OrderFactory(num_order_items=0)
        OrderItemFactory(
            order=order,
            product=product,
            price=Money(Decimal("12.40"), "EUR"),
            quantity=1,
        )
        breakdown = _compute_vat_breakdown(order)
        totals = _order_totals(order, breakdown)
        # Use a freshly-built Invoice instance — we don't need it saved
        # for this test, just the snapshot attributes.
        invoice = Invoice(
            order=order,
            invoice_number="INV-2026-000001",
            seller_snapshot={"name": "Test Seller"},
            buyer_snapshot={"name": "Test Buyer"},
            currency="EUR",
        )
        invoice.issue_date = order.created_at.date()

        ctx = _build_context(order, invoice, breakdown, totals)
        self.assertEqual(
            ctx["verification_url"],
            f"https://shop.example.com/account/orders/{order.id}",
        )
        self.assertIn("<svg", ctx["qr_svg"])
        # Rendering breakdown rows carry Decimals (not strings) so
        # ``floatformat`` in the template stays locale-aware.
        self.assertIsInstance(ctx["vat_breakdown"][0]["subtotal"], Decimal)
        # Persisted row is still string-shaped (JSON-friendly).
        self.assertIsInstance(breakdown[0]["subtotal"], str)
        # Items exposed as flat dicts, not model instances.
        self.assertIsInstance(ctx["items"][0], dict)
        self.assertIn("name", ctx["items"][0])
