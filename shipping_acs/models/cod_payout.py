"""ACS COD-payout reconciliation row.

ACS pays the partner (us) the cash collected at delivery on a
schedule.  ``ACS_COD_Beneficiary_Info`` lists every parcel pending
payout (or already paid) — we mirror those rows into our DB nightly
so accounting can reconcile against the original ``Order`` and
finance has a single source of truth.

One row per parcel per status snapshot; idempotency on
``voucher_no + cod_payment_date`` so the daily Celery task can rerun
safely.  A row ACS reported with no readable voucher is stored too
(``voucher_no`` empty) and keyed on ``row_fingerprint`` instead, so no
payout money is ever visible only in a log or an email.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from djmoney.models.fields import MoneyField
from djmoney.money import Money

from core.models import TimeStampMixinModel


class AcsCodPayout(TimeStampMixinModel):
    """One row per (voucher_no, COD payment date) pair."""

    voucher_no = models.CharField(
        _("Voucher number"),
        max_length=20,
        blank=True,
        help_text=_(
            "ACS voucher number — links back to AcsShipment.voucher_no "
            "without a hard FK so historic rows survive shipment "
            "deletions. Empty when ACS reported the row with no "
            "readable voucher."
        ),
    )
    shipment = models.ForeignKey(
        "shipping_acs.AcsShipment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cod_payouts",
        verbose_name=_("Shipment"),
        help_text=_(
            "Resolved at upsert time when the matching AcsShipment "
            "row exists. Null for orphan rows (parcels paid via ACS "
            "before they were minted through this system, or rows "
            "where the shipment has been hard-deleted)."
        ),
    )
    customer_code = models.CharField(
        _("ACS customer code"),
        max_length=32,
        blank=True,
        default="",
    )
    pod = models.CharField(
        _("Proof of delivery"),
        max_length=255,
        blank=True,
        default="",
    )
    parcel_sender = models.CharField(
        _("Parcel sender"),
        max_length=255,
        blank=True,
        default="",
    )
    parcel_receiver = models.CharField(
        _("Parcel receiver"),
        max_length=255,
        blank=True,
        default="",
    )
    parcel_pickup_date = models.DateTimeField(
        _("Parcel pickup date"), null=True, blank=True
    )
    parcel_delivery_date = models.DateTimeField(
        _("Parcel delivery date"), null=True, blank=True
    )
    customer_ref_no_1 = models.CharField(
        _("Customer reference 1"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Reference_Key1 from voucher creation — matches Order.id "
            "for our partner-side reconciliation."
        ),
    )
    customer_ref_no_2 = models.CharField(
        _("Customer reference 2"),
        max_length=64,
        blank=True,
        default="",
    )
    cod_amount_total = MoneyField(
        _("COD amount total"),
        max_digits=11,
        decimal_places=2,
        default=Money(0, "EUR"),
        default_currency="EUR",
        help_text=_("Parcel_COD_Amount from ACS."),
    )
    cod_amount_cash = MoneyField(
        _("COD amount cash"),
        max_digits=11,
        decimal_places=2,
        default=Money(0, "EUR"),
        default_currency="EUR",
    )
    cod_amount_card = MoneyField(
        _("COD amount card"),
        max_digits=11,
        decimal_places=2,
        default=Money(0, "EUR"),
        default_currency="EUR",
    )
    cod_payment_date = models.DateField(
        _("COD payment date"),
        null=True,
        blank=True,
        help_text=_(
            "Date ACS paid (or expects to pay) the partner. Forms half "
            "of the idempotency key together with voucher_no, or the "
            "row fingerprint when there is no voucher."
        ),
    )
    raw_payload = models.JSONField(
        _("Raw payload"),
        default=dict,
        blank=True,
        help_text=_("Full row from ACS_COD_Beneficiary_Info Table_Data."),
    )
    row_fingerprint = models.CharField(
        _("Row fingerprint"),
        max_length=64,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "SHA-256 of the ACS fields that identify the row. Set only "
            "for a row with no readable voucher: it is that row's "
            "idempotency key, so a rerun updates it instead of storing "
            "it twice."
        ),
    )
    reconciled_at = models.DateTimeField(
        _("Reconciled at"),
        null=True,
        blank=True,
        help_text=_(
            "When staff recorded that this payout was accounted for. A "
            "payout matching no shipment stays counted as needing "
            "attention until then."
        ),
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acs_cod_payouts_reconciled",
        verbose_name=_("Reconciled by"),
        # ORM-only, like AcsShipment.cod_received_outside_acs_by: the
        # operator is a PLATFORM-PUBLIC identity and this row lives in
        # the tenant schema.
        db_constraint=False,
        help_text=_(
            "Operator who reconciled the payout. Stores a "
            "PLATFORM-schema user id; not FK-enforced."
        ),
    )
    reconciliation_note = models.TextField(
        _("Reconciliation note"),
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "How the payout was accounted for (the off-system sale, the "
            "invoice, the ACS report line), as written by the operator."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS COD payout")
        verbose_name_plural = _("ACS COD payouts")
        ordering = ["-parcel_delivery_date", "-created_at"]
        constraints = [
            # One key per kind of row, so neither can collide with the
            # other: a row with a voucher is unique per voucher and date;
            # a row without one (voucher_no empty) is unique per
            # fingerprint.
            models.UniqueConstraint(
                fields=["voucher_no", "cod_payment_date"],
                condition=~models.Q(voucher_no=""),
                name="acs_cod_payout_voucher_date_uq",
            ),
            models.UniqueConstraint(
                fields=["row_fingerprint"],
                condition=~models.Q(row_fingerprint=""),
                name="acs_cod_payout_fingerprint_uq",
            ),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["voucher_no"], name="acs_cod_payout_voucher_ix"),
            BTreeIndex(
                fields=["cod_payment_date"],
                name="acs_cod_payout_paydate_ix",
            ),
            BTreeIndex(
                fields=["customer_ref_no_1"],
                name="acs_cod_payout_ref1_ix",
            ),
        ]

    def __str__(self) -> str:
        return gettext("ACS COD payout %(voucher)s (%(amount)s)") % {
            "voucher": self.voucher_no or gettext("without voucher"),
            "amount": self.cod_amount_total,
        }
