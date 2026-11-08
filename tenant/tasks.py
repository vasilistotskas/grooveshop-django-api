from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

from core import celery_app
from core.cache.edge import EdgePurgeError
from core.retry import (
    MAIL_TRANSIENT_ERRORS,
    TRANSIENT_ERRORS,
    TRANSIENT_HTTP_ERRORS,
    TRANSIENT_NETWORK_ERRORS,
)
from core.tasks import MonitoredTask, safe_task_arguments
from tenant.celery import TenantTask, run_for_all_tenants

if TYPE_CHECKING:
    from core.cache.edge import MediaPurge

logger = logging.getLogger(__name__)


@celery_app.task(base=TenantTask)
def fanout_reset_demo_stores():
    """Reset every demo store's shared accounts.

    Deliberately NOT ``run_for_all_tenants``: that helper dispatches to
    every active tenant, and this task DELETES orders, reviews, comments
    and carts. Against a real merchant it would destroy their customers'
    data. The ``is_demo`` filter is the safety property, so it is
    written out here rather than hidden behind a parameter.
    """
    from devtools.tasks import reset_demo_store_task
    from tenant.models import Tenant

    tenants = Tenant.objects.filter(
        is_active=True, is_demo=True, suspended_at__isnull=True
    ).exclude(schema_name="public")

    dispatched: list[dict[str, str]] = []
    for tenant in tenants:
        result = reset_demo_store_task.apply_async(
            args=[tenant.schema_name],
            ignore_result=reset_demo_store_task.ignore_result,
        )
        dispatched.append(
            {"schema": tenant.schema_name, "task_id": str(result.id)}
        )
    if not dispatched:
        logger.info("No demo tenants to reset")
    return dispatched


@celery_app.task(base=TenantTask)
def fanout_cleanup_abandoned_carts():
    return run_for_all_tenants("core.tasks.cleanup_abandoned_carts")


@celery_app.task(base=TenantTask)
def fanout_cleanup_old_guest_carts():
    return run_for_all_tenants("core.tasks.cleanup_old_guest_carts")


@celery_app.task(base=TenantTask)
def fanout_clear_expired_notifications():
    return run_for_all_tenants(
        "core.tasks.clear_expired_notifications_task", days=365
    )


@celery_app.task(base=TenantTask)
def fanout_send_inactive_user_notifications():
    return run_for_all_tenants("core.tasks.send_inactive_user_notifications")


@celery_app.task(base=TenantTask)
def fanout_clear_duplicate_history():
    return run_for_all_tenants(
        "core.tasks.clear_duplicate_history_task",
        excluded_fields=[],
        minutes=None,
    )


@celery_app.task(base=TenantTask)
def fanout_clear_old_history():
    return run_for_all_tenants("core.tasks.clear_old_history_task", days=365)


@celery_app.task(base=TenantTask)
def fanout_sync_meilisearch_indexes():
    return run_for_all_tenants("core.tasks.sync_meilisearch_indexes")


@celery_app.task(base=TenantTask)
def fanout_cleanup_expired_stock_reservations():
    return run_for_all_tenants("order.tasks.cleanup_expired_stock_reservations")


@celery_app.task(base=TenantTask)
def fanout_process_points_expiration():
    return run_for_all_tenants("loyalty.tasks.process_points_expiration")


# A publish the store scheduled: it goes live in the minute it is due.
@celery_app.task(base=TenantTask)
def fanout_run_due_builder_publishes():
    return run_for_all_tenants("builder.tasks.run_due_publishes")


# Frozen drafts that nobody can open any more: removed like the other
# sweeps, SUSPENDED tenants included.
@celery_app.task(base=TenantTask)
def fanout_purge_expired_builder_preview_links():
    return run_for_all_tenants(
        "builder.tasks.purge_expired_preview_links", include_suspended=True
    )


@celery_app.task(base=TenantTask)
def fanout_expire_gift_cards():
    return run_for_all_tenants("giftcard.tasks.expire_gift_cards")


@celery_app.task(base=TenantTask)
def fanout_deliver_scheduled_gift_cards():
    return run_for_all_tenants("giftcard.tasks.deliver_scheduled_gift_cards")


@celery_app.task(base=TenantTask)
def fanout_send_gift_card_expiry_reminders():
    return run_for_all_tenants("giftcard.tasks.send_gift_card_expiry_reminders")


@celery_app.task(base=TenantTask)
def fanout_auto_cancel_stuck_pending_orders():
    return run_for_all_tenants("order.tasks.auto_cancel_stuck_pending_orders")


@celery_app.task(base=TenantTask)
def fanout_complete_paid_delivered_orders():
    return run_for_all_tenants("order.tasks.complete_paid_delivered_orders")


@celery_app.task(base=TenantTask)
def fanout_reconcile_pending_refunds():
    return run_for_all_tenants("order.tasks.reconcile_pending_refunds")


@celery_app.task(base=TenantTask)
def fanout_send_checkout_abandonment_emails():
    return run_for_all_tenants("order.tasks.send_checkout_abandonment_emails")


@celery_app.task(base=TenantTask)
def fanout_send_review_request_emails():
    return run_for_all_tenants("order.tasks.send_review_request_emails")


@celery_app.task(base=TenantTask)
def fanout_submit_indexnow_urls():
    return run_for_all_tenants("indexnow.tasks.submit_pending_urls")


@celery_app.task(base=TenantTask)
def fanout_check_low_stock_products():
    return run_for_all_tenants("product.tasks.check_low_stock_products")


@celery_app.task(base=TenantTask)
def fanout_poll_acs_tracking_batch():
    return run_for_all_tenants("shipping_acs.tasks.poll_acs_tracking_batch")


@celery_app.task(base=TenantTask)
def fanout_reconcile_acs_cod_payouts():
    return run_for_all_tenants("shipping_acs.tasks.reconcile_acs_cod_payouts")


@celery_app.task(base=TenantTask)
def fanout_poll_boxnow_tracking_batch():
    return run_for_all_tenants(
        "shipping_boxnow.tasks.poll_boxnow_tracking_batch"
    )


@celery_app.task(base=TenantTask)
def fanout_sync_boxnow_lockers():
    return run_for_all_tenants("shipping_boxnow.tasks.sync_boxnow_lockers")


@celery_app.task(base=TenantTask)
def fanout_sync_acs_stations():
    return run_for_all_tenants("shipping_acs.tasks.sync_acs_stations")


@celery_app.task(base=TenantTask)
def fanout_issue_daily_acs_pickup_list():
    return run_for_all_tenants("shipping_acs.tasks.issue_daily_acs_pickup_list")


@celery_app.task(base=TenantTask)
def fanout_alert_unremitted_cod_payouts():
    # AcsShipment and AcsCodPayout are tenant-scoped, so beat must
    # dispatch per-schema or the check runs against an empty public one.
    return run_for_all_tenants(
        "shipping_acs.tasks.alert_unremitted_cod_payouts"
    )


@celery_app.task(base=TenantTask)
def fanout_anonymize_old_search_queries():
    return run_for_all_tenants(
        "search.tasks.anonymize_old_search_queries", days=90
    )


@celery_app.task(base=TenantTask)
def fanout_reindex_offer_window_changes():
    return run_for_all_tenants(
        "promotion.tasks.reindex_offer_window_changes_task"
    )


@celery_app.task(base=TenantTask)
def fanout_reevaluate_price_episodes():
    return run_for_all_tenants("product.tasks.reevaluate_price_episodes")


@celery_app.task(base=TenantTask)
def fanout_recompute_recommendation_candidates():
    return run_for_all_tenants("recommendation.tasks.recompute_all_candidates")


@celery_app.task(base=TenantTask)
def fanout_learn_recommendation_weights():
    return run_for_all_tenants("recommendation.tasks.learn_slot_weights")


@celery_app.task(base=TenantTask)
def fanout_update_click_scores():
    return run_for_all_tenants("search.tasks.update_click_scores")


# Both reach SUSPENDED tenants: these are retention sweeps, and an
# upload past its claim window or a file past the store's retention
# window is data the platform promised to delete. Freezing a store's
# billing does not freeze that promise.
@celery_app.task(base=TenantTask)
def fanout_reap_unclaimed_attachments():
    return run_for_all_tenants(
        "contact.tasks.reap_unclaimed_attachments", include_suspended=True
    )


@celery_app.task(base=TenantTask)
def fanout_rescan_pending_contact_attachments():
    return run_for_all_tenants("contact.tasks.rescan_pending_attachments")


@celery_app.task(base=TenantTask)
def fanout_purge_expired_attachment_files():
    return run_for_all_tenants(
        "contact.tasks.purge_expired_attachment_files",
        include_suspended=True,
    )


# Enquiries and feedback past the store's CONTACT_RETENTION_DAYS are
# personal data the platform promised to delete: reaches SUSPENDED
# tenants too, like the sweeps above.
@celery_app.task(base=TenantTask)
def fanout_purge_expired_contact_records():
    return run_for_all_tenants(
        "contact.tasks.purge_expired_contact_records",
        include_suspended=True,
    )


# A retention sweep like the ones above: the ACS call history holds
# recipient details, so it reaches SUSPENDED tenants too.
@celery_app.task(base=TenantTask)
def fanout_purge_expired_acs_api_calls():
    return run_for_all_tenants(
        "shipping_acs.tasks.purge_expired_acs_api_calls",
        include_suspended=True,
    )


# A retention sweep like the ones above, so it reaches SUSPENDED tenants
# too: an unconfirmed signup past its window is personal data kept
# without consent, whatever the store's billing state.
@celery_app.task(base=TenantTask)
def fanout_purge_unconfirmed_guest_subscriptions():
    return run_for_all_tenants(
        "user.tasks.purge_unconfirmed_guest_subscriptions",
        include_suspended=True,
    )


# Suspended stores too, for the same reason: a guest's alert left
# unconfirmed past its link's week is an address kept without consent.
# More retention sweeps, so they reach SUSPENDED tenants too: a store's
# billing state does not pause a deletion the platform promised.
@celery_app.task(base=TenantTask)
def fanout_purge_expired_acs_tracking_events():
    return run_for_all_tenants(
        "shipping_acs.tasks.purge_expired_acs_tracking_events",
        include_suspended=True,
    )


@celery_app.task(base=TenantTask)
def fanout_purge_expired_boxnow_parcel_events():
    return run_for_all_tenants(
        "shipping_boxnow.tasks.purge_expired_boxnow_parcel_events",
        include_suspended=True,
    )


@celery_app.task(base=TenantTask)
def fanout_purge_expired_recommendation_events():
    return run_for_all_tenants(
        "recommendation.tasks.purge_expired_recommendation_events",
        include_suspended=True,
    )


@celery_app.task(base=TenantTask)
def fanout_purge_expired_pending_payment_fees():
    return run_for_all_tenants(
        "order.tasks.purge_expired_pending_payment_fees",
        include_suspended=True,
    )


@celery_app.task(base=TenantTask)
def fanout_purge_expired_meta_capi_event_logs():
    return run_for_all_tenants(
        "meta_capi.tasks.purge_expired_meta_capi_event_logs",
        include_suspended=True,
    )


@celery_app.task(base=TenantTask)
def fanout_purge_expired_admin_log_entries():
    return run_for_all_tenants(
        "core.tasks.purge_expired_admin_log_entries",
        include_suspended=True,
    )


# The email log holds a masked address and the subject of every mail, so
# it reaches SUSPENDED tenants too.
@celery_app.task(base=TenantTask)
def fanout_purge_expired_email_logs():
    return run_for_all_tenants(
        "emails.tasks.purge_expired_email_logs",
        include_suspended=True,
    )


@celery_app.task(base=TenantTask)
def fanout_purge_unconfirmed_guest_alerts():
    return run_for_all_tenants(
        "product.tasks.purge_unconfirmed_guest_alerts",
        include_suspended=True,
    )


@celery_app.task(base=TenantTask)
def fanout_cleanup_expired_data_exports():
    return run_for_all_tenants("user.tasks.cleanup_expired_data_exports")


@celery_app.task(base=TenantTask)
def fanout_check_stale_acs_shipments():
    return run_for_all_tenants("shipping_acs.tasks.check_stale_acs_shipments")


@celery_app.task(base=TenantTask)
def fanout_clear_expired_sessions():
    # django_session is per-schema (sessions is dual-listed), so
    # ``clearsessions`` only ever purges the schema it runs in. The beat
    # scheduler fires the underlying task once in PUBLIC (platform-staff
    # sessions live there); this fans it into every tenant schema so
    # each store's expired customer sessions are purged too — without
    # it, tenant ``django_session`` tables grow unbounded.
    return run_for_all_tenants("core.tasks.clear_expired_sessions_task")


# NOT a fanout: billing terms, dunning bookkeeping, and outbound
# platform mail all live on the PUBLIC schema (Tenant rows), so the
# whole estate is processed in one public-schema pass. Defined here —
# not in tenant/billing.py — because Celery autodiscovery only scans
# ``tasks.py`` modules.
@celery_app.task(
    base=MonitoredTask,
    max_retries=3,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
)
def process_tenant_billing():
    from tenant.billing import run_billing_cycle

    return run_billing_cycle()


# NOT a fanout and NOT beat-scheduled: dispatched on demand from
# tenant.lifecycle.suspend_tenant and destroy_tenant with a specific schema
# name. Runs in public (it makes an HTTP call to media-stream with the
# schema as a string, then one to Cloudflare per zone; the only table it
# reads is the public Tenant row), so no _schema_name is needed.
#
# ``edge_purges`` is planned by the caller while the tenant row still
# exists. It names zones; a tenant's own zone also carries its token as the
# Fernet ciphertext the database stores, never in clear, so the purge still
# runs after a destroy removed the row (core.cache.edge.purge_media).
@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(*TRANSIENT_ERRORS, *TRANSIENT_HTTP_ERRORS, EdgePurgeError),
    retry_backoff=True,
    retry_jitter=True,
)
def flush_tenant_media_task(
    schema_name: str, edge_purges: list[MediaPurge] | None = None
):
    from core.cache import edge
    from tenant.media_flush import flush_tenant_media

    flushed = flush_tenant_media(schema_name)
    # After the flush, never before: see edge.purge_media.
    edge.purge_media(schema_name, edge_purges or [])
    return {"flushed": flushed, "edge_zones": len(edge_purges or [])}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=TRANSIENT_ERRORS,
    retry_backoff=True,
)
def purge_expired_tenant_archives(self) -> dict:
    """Erase destroyed tenants' invoices once their retention expires.

    The other half of the offboarding trade. Invoices survive a store's
    destruction because a legal obligation overrides erasure (GDPR art.
    17(3)(b)); once that obligation lapses the exception lapses with it,
    and keeping them becomes a storage-limitation breach in its own
    right (art. 5(1)(e)). Retaining forever is not the safe option — it
    is the opposite failure.

    Runs in the PUBLIC schema: ``TenantArchive`` lives there precisely
    because the schemas it describes no longer exist.
    """
    from django.utils import timezone

    from tenant.models import TenantArchive
    from tenant.offboarding import _remove_tree

    today = timezone.now().date()
    due = TenantArchive.objects.filter(
        purged_at__isnull=True,
        retention_until__isnull=False,
        retention_until__lte=today,
    )

    purged = []
    for archive in due:
        if archive.retained_invoice_path:
            _remove_tree(
                archive.retained_invoice_path,
                what="retained invoices (retention expired)",
                schema_name=archive.schema_name,
            )
        archive.purged_at = timezone.now()
        archive.save(update_fields=["purged_at", "updated_at"])
        purged.append(archive.schema_name)

    if purged:
        logger.info(
            "Purged expired retention for %d tenant archive(s): %s",
            len(purged),
            ", ".join(purged),
        )
    return {"status": "success", "purged": purged}


@celery_app.task(base=MonitoredTask)
def refresh_stats_snapshot() -> dict:
    """Record this store's headline figures on its ``TenantStatsSnapshot``.

    Runs inside the store's schema (``TenantTask``) - the only place its
    orders can be read - and writes the public-schema snapshot the
    control plane reads instead of switching schema per store per page.
    No retries: the schedule runs it again within half an hour.
    """
    from django.db.models import Count, Max, Q, Sum
    from django.utils import timezone

    from order.enum.status import PaymentStatus
    from order.models.order import Order
    from tenant.membership import resolve_current_tenant
    from tenant.models import TenantStatsSnapshot

    tenant = resolve_current_tenant()
    if tenant is None:
        raise RuntimeError(
            "refresh_stats_snapshot runs inside a store's schema; "
            "dispatch it through fanout_refresh_stats_snapshots"
        )
    figures = Order.objects.aggregate(
        orders=Count("id"),
        revenue=Sum(
            "amount_due", filter=Q(payment_status=PaymentStatus.COMPLETED)
        ),
        last_order_at=Max("created_at"),
    )
    TenantStatsSnapshot.objects.update_or_create(
        tenant=tenant,
        defaults={
            "orders_count": figures["orders"],
            "revenue": figures["revenue"] or 0,
            "last_order_at": figures["last_order_at"],
            "refreshed_at": timezone.now(),
        },
    )
    return {"schema": tenant.schema_name, "orders": figures["orders"]}


@celery_app.task(base=TenantTask)
def fanout_refresh_stats_snapshots():
    """Suspended stores are skipped and keep their last snapshot."""
    return run_for_all_tenants("tenant.tasks.refresh_stats_snapshot")


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    autoretry_for=MAIL_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def send_staff_password_reset_email(
    self,
    *,
    email: str,
    host: str,
    secure: bool,
    site_name: str,
    urlconf: str,
) -> dict[str, int]:
    """Mail the admin "forgot password" link to the staff accounts on
    ``email`` that can sign in to the admin that asked.

    Queued for EVERY accepted request, found address or not, so the
    request never learns whether the address is an account (see
    ``admin.password_reset``). It runs in the schema of the host that
    asked, which decides the language: the store's own default locale on a
    store host, the project default on the platform host. The platform
    sends the mail (``platform.staff_password_reset``) and the link points
    at the host the request came in on.
    """
    from django.contrib.auth.tokens import default_token_generator
    from django.urls import reverse
    from django.utils import translation
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from admin.password_reset import StaffPasswordResetForm
    from emails.send import send_email
    from tenant.credentials import tenant_site_name
    from tenant.emails import StaffPasswordReset
    from tenant.membership import bound_tenant

    tenant = bound_tenant()
    language = getattr(tenant, "default_locale", None) or settings.LANGUAGE_CODE
    # The mail is built under ``public`` (the platform sends it), which
    # knows only the platform's name: the store is named from this schema.
    store_name = tenant_site_name()
    sent = 0
    for user in StaffPasswordResetForm().get_users(email):
        # The link is built in the store's language: the admin's URLs carry
        # a language prefix, and the page it opens answers in that language.
        with translation.override(language):
            path = reverse(
                "admin:admin_password_reset_confirm",
                kwargs={
                    "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                    "token": default_token_generator.make_token(user),
                },
                current_app=site_name,
                urlconf=urlconf,
            )
        send_email(
            "platform.staff_password_reset",
            StaffPasswordReset(
                address=user.email,
                language=language,
                reset_url=f"{'https' if secure else 'http'}://{host}{path}",
                valid_minutes=settings.PASSWORD_RESET_TIMEOUT // 60,
                store_name=store_name,
            ),
        )
        sent += 1
    return {"sent": sent}


#: The advisory lock one sweep of deferred dispatches holds while it runs.
_SWEEP_LOCK = "tenant.redispatch_deferred_tasks"


@celery_app.task(base=MonitoredTask)
def redispatch_deferred_tasks() -> dict[str, int]:
    """Publish again the tasks whose publish failed after their change
    committed (``tenant.celery.dispatch_on_commit``).

    Runs in ``public``, where the rows live. A row is deleted once its task is
    out; one that fails again waits ``base * 2**attempts`` seconds (capped); a
    row older than the retention is deleted with an ERROR, because the work it
    stood for was not done.

    One sweep runs at a time, and the publishes run outside any transaction.
    A publish can wait without end: while RabbitMQ blocks publishers (a memory
    or disk alarm) the producer waits for its confirm, and closing the channel
    after a failed publish waits too. Measured against a broker with its
    memory alarm on: still waiting after 75 s, with a publish timeout or
    without. No lease is long enough for that, and a transaction would be
    ended by ``idle_in_transaction_session_timeout``, so a sweep holds a
    session-level advisory lock and a sweep that finds it held does nothing.
    The lock goes with the session if the worker dies, and the next sweep
    sends again at most the one row that was going out.

    A healthy sweep takes seconds and beat sends one a minute, so a sweep
    that finds the lock held is a sign of trouble and says so at WARNING,
    with what is waiting. The connection pool resets nothing on a returned
    connection, so a lock left held would stop every sweep until the pool
    retires that connection: an unlock that releases nothing is an ERROR.
    """
    from django.db import connection
    from django.utils import timezone

    from tenant.models import DeferredDispatch

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
            [_SWEEP_LOCK],
        )
        (held,) = cursor.fetchone()
    if not held:
        waiting = DeferredDispatch.objects.filter(
            next_attempt_at__lte=timezone.now()
        )
        oldest = (
            waiting.order_by("created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        logger.warning(
            "A sweep of deferred dispatches is still running, so this one did "
            "nothing; %s due, the oldest kept at %s",
            waiting.count(),
            oldest or "-",
        )
        return {"published": 0, "failed": 0, "expired": 0}
    try:
        return _redispatch_due()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                [_SWEEP_LOCK],
            )
            (released,) = cursor.fetchone()
        if not released:
            logger.error(
                "The deferred dispatch sweep ended without holding its lock; "
                "if another session holds it, no sweep runs until that "
                "connection is closed"
            )


def _redispatch_due() -> dict[str, int]:
    from datetime import timedelta

    from django.utils import timezone

    from tenant.models import DeferredDispatch

    now = timezone.now()
    done = failed = expired = 0
    expired_before = now - timedelta(
        days=settings.DEFERRED_DISPATCH_RETENTION_DAYS
    )
    due = list(
        DeferredDispatch.objects.filter(next_attempt_at__lte=now).order_by(
            "next_attempt_at", "id"
        )[: settings.DEFERRED_DISPATCH_BATCH]
    )
    for row in due:
        task = celery_app.tasks.get(row.task_name)
        if row.created_at < expired_before:
            safe_args, safe_kwargs = safe_task_arguments(
                task, row.args, row.kwargs
            )
            logger.error(
                "Deferred dispatch of %s for %s given up after %s "
                "attempts: %s; payload args=%s kwargs=%s",
                row.task_name,
                row.schema_name or "-",
                row.attempts,
                row.last_error,
                safe_args,
                safe_kwargs,
            )
            row.delete()
            expired += 1
            continue
        try:
            if task is None:
                raise LookupError(f"no task named {row.task_name!r}")
            task.apply_async(
                args=row.args,
                kwargs=row.kwargs,
                headers=row.headers,
                **row.options,
            )
        except Exception as exc:
            broker_down = isinstance(exc, TRANSIENT_NETWORK_ERRORS)
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:255]
            row.next_attempt_at = now + timedelta(
                seconds=min(
                    settings.DEFERRED_DISPATCH_BACKOFF_SECONDS
                    * 2**row.attempts,
                    settings.DEFERRED_DISPATCH_BACKOFF_CAP_SECONDS,
                )
            )
            row.save(
                update_fields=["attempts", "last_error", "next_attempt_at"]
            )
            failed += 1
            if broker_down:
                # Every other row would wait out the same timeout; they stay
                # due for the next run.
                break
        else:
            row.delete()
            done += 1
    if failed:
        logger.error(
            "%s deferred dispatch(es) could not be published again", failed
        )
    return {"published": done, "failed": failed, "expired": expired}
