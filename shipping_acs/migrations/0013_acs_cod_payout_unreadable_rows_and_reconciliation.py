"""Store payout rows with no readable voucher; let staff reconcile a row.

Every column added is nullable or carries ``db_default``, so the release
still serving never names them and its INSERTs keep working.

The ``(voucher_no, cod_payment_date)`` unique constraint becomes partial
(``voucher_no <> ''``) and a partial unique constraint on
``row_fingerprint`` is added, in this one transaction. Making a unique
constraint narrower only loosens what the old release can insert, it never
removes a table or column, and the replacement is rebuilt over rows that
already satisfied the wider one, so it cannot fail on existing data. The
table holds one row per COD parcel paid, so the rebuild is instantaneous.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shipping_acs', '0012_acsshipment_cod_received_outside_acs'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='acscodpayout',
            name='acs_cod_payout_voucher_date_uq',
        ),
        migrations.AddField(
            model_name='acscodpayout',
            name='reconciled_at',
            field=models.DateTimeField(blank=True, help_text='When staff recorded that this payout was accounted for. A payout matching no shipment stays counted as needing attention until then.', null=True, verbose_name='Reconciled at'),
        ),
        migrations.AddField(
            model_name='acscodpayout',
            name='reconciled_by',
            field=models.ForeignKey(blank=True, db_constraint=False, help_text='Operator who reconciled the payout. Stores a PLATFORM-schema user id; not FK-enforced.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='acs_cod_payouts_reconciled', to=settings.AUTH_USER_MODEL, verbose_name='Reconciled by'),
        ),
        migrations.AddField(
            model_name='acscodpayout',
            name='reconciliation_note',
            field=models.TextField(blank=True, db_default='', default='', help_text='How the payout was accounted for (the off-system sale, the invoice, the ACS report line), as written by the operator.', verbose_name='Reconciliation note'),
        ),
        migrations.AddField(
            model_name='acscodpayout',
            name='row_fingerprint',
            field=models.CharField(blank=True, db_default='', default='', help_text="SHA-256 of the ACS fields that identify the row. Set only for a row with no readable voucher: it is that row's idempotency key, so a rerun updates it instead of storing it twice.", max_length=64, verbose_name='Row fingerprint'),
        ),
        migrations.AlterField(
            model_name='acscodpayout',
            name='cod_payment_date',
            field=models.DateField(blank=True, help_text='Date ACS paid (or expects to pay) the partner. Forms half of the idempotency key together with voucher_no, or the row fingerprint when there is no voucher.', null=True, verbose_name='COD payment date'),
        ),
        migrations.AlterField(
            model_name='acscodpayout',
            name='voucher_no',
            field=models.CharField(blank=True, help_text='ACS voucher number — links back to AcsShipment.voucher_no without a hard FK so historic rows survive shipment deletions. Empty when ACS reported the row with no readable voucher.', max_length=20, verbose_name='Voucher number'),
        ),
        migrations.AddConstraint(
            model_name='acscodpayout',
            constraint=models.UniqueConstraint(condition=models.Q(('voucher_no', ''), _negated=True), fields=('voucher_no', 'cod_payment_date'), name='acs_cod_payout_voucher_date_uq'),
        ),
        migrations.AddConstraint(
            model_name='acscodpayout',
            constraint=models.UniqueConstraint(condition=models.Q(('row_fingerprint', ''), _negated=True), fields=('row_fingerprint',), name='acs_cod_payout_fingerprint_uq'),
        ),
    ]
