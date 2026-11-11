"""The one VAT rule behind every figure an order bills.

The PDF invoice (``order.invoicing``), the ``Invoice`` row it persists and
the myDATA payload (``order.mydata.builder``) all derive their lines here,
so their net, VAT and gross totals are the same numbers by construction —
not two computations that happen to agree.

Goods
    One line per ``OrderItem``: its VAT-inclusive gross, less the part of
    the order's discount **snapshotted on the line at checkout**
    (``OrderItem.discount_allocation``, see ``order.discounts``: a promotion
    lands on the lines it covers), split at the rate **snapshotted on the
    line at checkout** (``OrderItem.vat_rate``). The catalogue is never
    consulted: editing or deleting a ``Vat`` row later must not rewrite a
    tax document that was already earned.

Shipping and the payment-method fee
    Incidental costs the seller charges the customer are part of the
    taxable amount of the supply they accompany (Directive 2006/112/EC
    art. 78(b); Greek VAT Code, Law 5144/2024, art. 24(4)(a)), so they are
    taxed at the rate of the goods they travel with. An order that mixes
    rates has no single rate to give them: each charge is apportioned over
    the rates in proportion to the value of the goods charged at each, to
    the cent (largest remainder), and every share is taxed at its own rate.
    The key is the one AADE circular Εγκ. 10/1987 (Π.4336/3162) gives for
    incidental costs, "με βάση την αξία των αγαθών": the value of the goods
    actually charged, so AFTER the discounts they received. A discount no
    longer lands proportionally on every line, so the pre-discount value
    would weigh a rate by goods the customer did not pay for. A
    single-rate order degenerates to 100% at that rate.

Rounding
    Every line is split into a net, rounded to the cent (``ROUND_HALF_UP``,
    AADE's rule), and the VAT that is left of its gross, so each line adds
    up to what was charged. This happens **before** anything is summed:
    myDATA validates ``invoiceSummary`` against the sum of the rounded
    lines, so the PDF totals must be that sum too — summing the lines
    first and rounding the bucket gives a different cent on, for example,
    three €1.00 lines at 24%.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from order.discounts import CENT, discounted_line_gross, proportional_shares


class ChargeKind(models.TextChoices):
    GOODS = "goods", _("Goods")
    SHIPPING = "shipping", _("Shipping")
    PAYMENT_FEE = "payment_fee", _("Payment fee")


@dataclass(frozen=True)
class VatLine:
    """One taxable line: its rate and its net and VAT amounts, to the cent.

    ``item_pk`` is the ``OrderItem`` a goods line was taken from; shipping
    and the fee belong to no item.
    """

    kind: ChargeKind
    rate: Decimal
    net: Decimal
    vat: Decimal
    item_pk: int | None = None

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat


def split_gross(gross: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]:
    """Back out net + VAT from a cent-exact, VAT-inclusive amount.

    The net is rounded to the cent (half up) and the VAT is what is left,
    so net + VAT is ``gross`` on every line. Rounding both independently
    breaks that on a half-cent tie: 0.13 at 4% has a net of 0.125, which
    rounds up to 0.13, and a VAT of 0.005, which also rounds up to 0.01,
    so the line would bill 0.14 for 0.13 of goods. The per-line and
    summary columns must agree after AADE's server-side re-rounding
    (errors 203 / 207-210 otherwise).
    """
    net = (gross / (Decimal(1) + rate / Decimal(100))).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return net, gross - net


def _line(
    kind: ChargeKind,
    gross: Decimal,
    rate: Decimal,
    item_pk: int | None = None,
) -> VatLine:
    net, vat = split_gross(gross, rate)
    return VatLine(kind=kind, rate=rate, net=net, vat=vat, item_pk=item_pk)


@dataclass(frozen=True)
class Goods:
    """What the VAT rule reads of one order line: the plain values, so the
    live models and a migration's historical ones feed the same code."""

    pk: int
    rate: Decimal
    quantity: int
    gross: Decimal
    """Charged for, after the line's snapshotted discount."""


def _rate_weights(goods: Sequence[Goods]) -> dict[Decimal, Decimal]:
    """What each VAT rate weighs in the order's goods.

    Value comes first: the gross the goods were charged for, after their
    discounts (Εγκ. 10/1987, "the value of the goods"). An order whose
    goods carry no value at all (a free sample with paid delivery, a fully
    discounted basket) falls back to units, so the charge still follows
    the goods it travels with.
    """
    value: dict[Decimal, Decimal] = defaultdict(Decimal)
    units: dict[Decimal, Decimal] = defaultdict(Decimal)
    for line in goods:
        value[line.rate] += line.gross
        units[line.rate] += line.quantity
    return {rate: weight for rate, weight in value.items() if weight > 0} or {
        rate: weight for rate, weight in units.items() if weight > 0
    }


def vat_lines(
    goods: Sequence[Goods],
    *,
    shipping: Decimal,
    payment_fee: Decimal,
    order_pk: int,
) -> list[VatLine]:
    """Every taxable line of an order: goods, then shipping, then the fee.

    The one place the rule is applied, to plain values: ``order_vat_lines``
    reads them off the live order, ``order.vat_snapshot`` off whatever
    models it is given. A zero-gross line is left out.
    """
    lines = [
        _line(ChargeKind.GOODS, line.gross, line.rate, line.pk)
        for line in goods
        if line.gross > 0
    ]

    charges = (
        (ChargeKind.SHIPPING, shipping),
        (ChargeKind.PAYMENT_FEE, payment_fee),
    )
    weights = _rate_weights(goods)
    for kind, amount in charges:
        if amount <= 0:
            continue
        if not weights:
            raise ValueError(
                f"Order {order_pk} charges {kind.value.replace('_', ' ')} "
                "but has no goods for it to follow, so no VAT rate applies."
            )
        shares = proportional_shares(amount, weights)
        lines.extend(
            _line(kind, share, rate)
            for rate, share in sorted(shares.items(), reverse=True)
            if share > 0
        )
    return lines


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
    return vat_lines(
        [
            Goods(
                pk=item.pk,
                rate=item.vat_rate,
                quantity=item.quantity,
                gross=gross_by_pk[item.pk],
            )
            for item in rows
        ],
        shipping=Decimal(order.shipping_price.amount),
        payment_fee=Decimal(order.payment_method_fee.amount),
        order_pk=order.pk,
    )


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
