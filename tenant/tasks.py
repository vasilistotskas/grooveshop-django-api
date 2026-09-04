from __future__ import annotations

import logging

from core import celery_app
from core.tasks import MonitoredTask
from tenant.celery import TenantTask, run_for_all_tenants

logger = logging.getLogger(__name__)


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
def fanout_check_pending_orders():
    return run_for_all_tenants("order.tasks.check_pending_orders")


@celery_app.task(base=TenantTask)
def fanout_auto_cancel_stuck_pending_orders():
    return run_for_all_tenants("order.tasks.auto_cancel_stuck_pending_orders")


@celery_app.task(base=TenantTask)
def fanout_send_checkout_abandonment_emails():
    return run_for_all_tenants("order.tasks.send_checkout_abandonment_emails")


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
def fanout_warn_unprinted_acs_vouchers():
    return run_for_all_tenants("shipping_acs.tasks.warn_unprinted_acs_vouchers")


@celery_app.task(base=TenantTask)
def fanout_anonymize_old_search_queries():
    return run_for_all_tenants(
        "search.tasks.anonymize_old_search_queries", days=90
    )


@celery_app.task(base=TenantTask)
def fanout_update_click_scores():
    return run_for_all_tenants("search.tasks.update_click_scores")


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
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def process_tenant_billing():
    from tenant.billing import run_billing_cycle

    return run_billing_cycle()


# NOT a fanout and NOT beat-scheduled: dispatched on demand from
# tenant.lifecycle.suspend_tenant with a specific schema name. Runs in
# public (it only makes an HTTP call to media-stream with the schema as
# a string — no tenant-schema access), so no _schema_name is needed.
@celery_app.task(
    base=MonitoredTask,
    max_retries=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
)
def flush_tenant_media_task(schema_name: str):
    from tenant.media_flush import flush_tenant_media

    return {"flushed": flush_tenant_media(schema_name)}


@celery_app.task(
    base=MonitoredTask,
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
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
