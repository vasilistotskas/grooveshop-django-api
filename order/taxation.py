"""The one VAT rule behind every figure an order bills.

The PDF invoice (``order.invoicing``), the ``Invoice`` row it persists and
the myDATA payload (``order.mydata.builder``) all derive their lines here,
so their net, VAT and gross totals are the same numbers by construction —
not two computations that happen to agree.

Goods
    One line per ``OrderItem``: its VAT-inclusive gross, after the
    order-level discount allocation (``order.discounts``), split at the
    rate **snapshotted on the line at checkout** (``OrderItem.vat_rate``).
    The catalogue is never consulted: editing or deleting a ``Vat`` row
    later must not rewrite a tax document that was already earned.

Shipping and the payment-method fee
    Incidental costs the seller charges the customer are part of the
    taxable amount of the supply they accompany (Directive 2006/112/EC
    art. 78(b); Greek VAT Code, Law 5144/2024, art. 24(4)(a)), so they are
    taxed at the rate of the goods they travel with. An order that mixes
    rates has no single rate to give them: each charge is apportioned over
    the rates in proportion to the value the goods carry at each, to the
    cent (largest remainder), and every share is taxed at its own rate.
    Neither law fixes the apportionment key; value is the neutral one and
    is what a single-rate order degenerates to (100% at that rate).

Rounding
    Every line is split into a net and a VAT amount rounded to the cent
    (``ROUND_HALF_UP``, AADE's rule) **before** anything is summed. myDATA
    validates ``invoiceSummary`` against the sum of the rounded lines, so
    the PDF totals must be that sum too — summing the lines first and
    rounding the bucket gives a different cent on, for example, three
    €1.00 lines at 24%.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from order.discounts import CENT, discounted_line_gross, proportional_shares


class ChargeKind(StrEnum):
    GOODS = "goods"
    SHIPPING = "shipping"
    PAYMENT_FEE = "payment_fee"


@dataclass(frozen=True)
class VatLine:
    """One taxable line: its rate and its net and VAT amounts, to the cent."""

    kind: ChargeKind
    rate: Decimal
    net: Decimal
    vat: Decimal

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat


def split_gross(gross: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Back out net + VAT from a VAT-inclusive amount, each to the cent.

    Returning the ROUNDED values is essential: the per-line and summary
    columns must agree after AADE's server-side re-rounding (errors 203 /
    207-210 otherwise).
    """
    net = gross / (Decimal(1) + rate / Decimal(100))
    return (
        net.quantize(CENT, rounding=ROUND_HALF_UP),
        (gross - net).quantize(CENT, rounding=ROUND_HALF_UP),
    )


def _line(kind: ChargeKind, gross: Decimal, rate: Decimal) -> VatLine:
    net, vat = split_gross(gross, rate)
    return VatLine(kind=kind, rate=rate, net=net, vat=vat)


def _rate_weights(items: Sequence[Any]) -> dict[Decimal, Decimal]:
    """What each VAT rate weighs in the order's goods.

    Value comes first: the gross the goods were sold for, before the
    order-level discount (which is spread proportionally over the lines,
    so it never changes the proportions). An order whose goods carry no
    value at all (a free sample with paid delivery) falls back to units,
    so the charge still follows the goods it travels with.
    """
    value: dict[Decimal, Decimal] = defaultdict(Decimal)
    units: dict[Decimal, Decimal] = defaultdict(Decimal)
    for item in items:
        value[item.vat_rate] += Decimal(item.price.amount) * item.quantity
        units[item.vat_rate] += item.quantity
    return {rate: weight for rate, weight in value.items() if weight > 0} or {
        rate: weight for rate, weight in units.items() if weight > 0
    }


def order_vat_lines(
    order: Any, items: Sequence[Any] | None = None
) -> list[VatLine]:
    """Every taxable line of ``order``: goods, then shipping, then the fee.

    A zero-gross line (a free gift, a fully discounted line) is left out:
    myDATA rejects zero-amount lines and a breakdown row of zeros says
    nothing. ``items`` lets a caller pass rows it already fetched.
    """
    rows = list(order.items.all()) if items is None else list(items)
    gross_by_pk = discounted_line_gross(order, rows)
    lines = [
        _line(ChargeKind.GOODS, gross_by_pk[item.pk], item.vat_rate)
        for item in rows
        if gross_by_pk[item.pk] > 0
    ]

    charges = (
        (ChargeKind.SHIPPING, order.shipping_price),
        (ChargeKind.PAYMENT_FEE, order.payment_method_fee),
    )
    weights = _rate_weights(rows)
    for kind, money in charges:
        if money.amount <= 0:
            continue
        if not weights:
            raise ValueError(
                f"Order {order.pk} charges {kind.value.replace('_', ' ')} "
                "but has no goods for it to follow, so no VAT rate applies."
            )
        shares = proportional_shares(Decimal(money.amount), weights)
        lines.extend(
            _line(kind, share, rate)
            for rate, share in sorted(shares.items(), reverse=True)
            if share > 0
        )
    return lines


def breakdown_by_rate(lines: Iterable[VatLine]) -> list[dict[str, str]]:
    """One row per VAT rate, highest first — the shape stored on an invoice.

    Values are strings so they round-trip through JSON storage losslessly.
    """
    buckets: dict[Decimal, dict[str, Decimal]] = defaultdict(
        lambda: {"subtotal": Decimal(0), "vat": Decimal(0)}
    )
    for line in lines:
        buckets[line.rate]["subtotal"] += line.net
        buckets[line.rate]["vat"] += line.vat
    return [
        {
            "rate": str(rate),
            "subtotal": str(values["subtotal"]),
            "vat": str(values["vat"]),
            "gross": str(values["subtotal"] + values["vat"]),
        }
        for rate, values in sorted(buckets.items(), reverse=True)
    ]
