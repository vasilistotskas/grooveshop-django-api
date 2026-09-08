"""Replay the order-status transition for shipments the poll can't revisit.

``poll_acs_tracking`` only dispatches for NON-terminal shipments
(``shipping_acs/tasks.py``: it excludes PENDING_CREATION, DELIVERED,
RETURNED, CANCELED and LOST). That is correct — a delivered parcel has
nothing left to report — but it means the order status is applied exactly
once, at the moment the shipment reaches its terminal state. If that
single attempt failed, the order is stranded forever: the poll will never
look at that shipment again, and ``check_stale_acs_shipments`` excludes
terminal states too, so nothing even complains.

That is not hypothetical. On 2026-09-08 production held nine orders in
PROCESSING whose ACS parcel had come back months earlier — 67, 68, 97, 98,
107, 120, 126, 135 and 156, the oldest returned 2026-05-29. All nine
predate the SHIPPED-bridging fix in
``AcsService._apply_order_status_transition``: the state machine forbids
PROCESSING -> RETURNED, every poll's attempt was rejected, and by the time
bridging existed the shipments were terminal and out of scope.

Deliberately a command, not a task. This repairs history, and history
should be repaired when someone is watching: it defaults to a dry run and
writes only under ``--apply``.

Customer notifications are suppressed. These transitions are months late;
"your order was returned" about a July parcel would confuse, not inform.
Internal state still flows — history rows, signals, the state machine.

Stock is NOT touched. A RETURNED transition never restored stock (only
``cancel_order`` does), so whether the returned goods went back on the
shelf is a separate, physical question. Confirmed with the site owner on
2026-09-08 that stock was already correct for those nine.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from core.management.tenant_mixin import TenantCommandMixin


class Command(TenantCommandMixin, BaseCommand):
    help = (
        "Re-apply the order-status transition for ACS shipments already in "
        "a terminal state whose order disagrees (dry run unless --apply)."
    )

    # Orders and shipments are tenant-schema tables; the public schema
    # holds pre-multi-tenant legacy rows at best.
    require_tenant_scope = True

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Persist the transitions. Without it the command only "
                "reports what it would change."
            ),
        )
        parser.add_argument(
            "--order",
            type=int,
            action="append",
            dest="orders",
            help=(
                "Limit to this order id (repeatable). Omit to consider "
                "every terminal shipment in the schema."
            ),
        )
        self.add_tenant_arguments(parser)

    def handle(self, *args, **options):
        for ctx in self.iter_tenant_contexts(options):
            with ctx:
                self._handle_for_schema(*args, **options)

    def _handle_for_schema(self, *args, **options):
        from shipping_acs.enum.shipment_state import AcsShipmentState
        from shipping_acs.models import AcsShipment
        from shipping_acs.services import AcsService

        # The states poll_acs_tracking excludes, minus PENDING_CREATION
        # (no voucher was ever minted, so there is nothing to replay).
        terminal_states = [
            AcsShipmentState.DELIVERED,
            AcsShipmentState.RETURNED,
            AcsShipmentState.CANCELED,
            AcsShipmentState.LOST,
        ]

        apply_changes: bool = options["apply"]
        only_orders: list[int] | None = options.get("orders")
        schema = connection.schema_name

        queryset = AcsShipment.objects.filter(
            shipment_state__in=terminal_states,
            order__isnull=False,
        ).select_related("order")
        if only_orders:
            queryset = queryset.filter(order_id__in=only_orders)

        drifted = []
        for shipment in queryset.order_by("order_id"):
            order = shipment.order
            before = order.status
            # The SAME function the live poll uses to decide, asked
            # without writing. A None means order and shipment already
            # agree, or the order is somewhere the mapping won't move it.
            expected = AcsService.order_status_for_shipment_state(
                shipment.shipment_state, before
            )
            if expected is None:
                continue
            drifted.append((shipment, order, before, expected))

        if not drifted:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{schema}: every terminal ACS shipment agrees with its "
                    "order status."
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"{schema}: {len(drifted)} order(s) disagree with a terminal "
                "shipment state."
            )
        )
        for shipment, order, before, expected in drifted:
            self.stdout.write(
                f"  order {order.id}: {before} -> {expected}  "
                f"(shipment {shipment.voucher_no or '-'} is "
                f"{shipment.shipment_state}, payment {order.payment_status})"
            )

        if not apply_changes:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run — nothing written. Re-run with --apply to "
                    "persist these transitions."
                )
            )
            return

        applied = 0
        failed = 0
        for shipment, order, before, expected in drifted:
            try:
                AcsService._apply_order_status_transition(
                    order,
                    shipment.shipment_state,
                    silent_for_customer=True,
                )
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"  order {order.id}: {type(exc).__name__}: {exc}"
                    )
                )
                continue
            # ``values_list``, never ``refresh_from_db(fields=["status"])``
            # — the same trap ``OrderService.maybe_advance_to_completed``
            # documents. A partial refresh leaves the other columns
            # DEFERRED, and ``Order.__init__`` reads them while it
            # snapshots ``_original_status`` / ``_original_payment_status``
            # / ``_original_tracking_number``, so each lazy load
            # re-instantiates through the manager and recurses until the
            # stack ends. Caught here by the tests below.
            after = (
                AcsShipment.objects.filter(pk=shipment.pk)
                .values_list("order__status", flat=True)
                .first()
            )
            if after == before:
                # The state machine refused and logged why; surface it
                # here too rather than reporting a silent success.
                failed += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"  order {order.id}: still {before} — the state "
                        "machine rejected the transition, see the "
                        "shipping_acs.services warnings"
                    )
                )
                continue
            applied += 1
            self.stdout.write(
                self.style.SUCCESS(f"  order {order.id}: {before} -> {after}")
            )

        summary = f"{schema}: {applied} transitioned, {failed} unresolved"
        self.stdout.write(
            (self.style.WARNING if failed else self.style.SUCCESS)(summary)
        )
