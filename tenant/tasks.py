from __future__ import annotations

import logging

from core import celery_app
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
def fanout_check_pending_orders():
    return run_for_all_tenants("order.tasks.check_pending_orders")


@celery_app.task(base=TenantTask)
def fanout_update_order_statuses_from_shipping():
    return run_for_all_tenants(
        "order.tasks.update_order_statuses_from_shipping"
    )


@celery_app.task(base=TenantTask)
def fanout_auto_cancel_stuck_pending_orders():
    return run_for_all_tenants("order.tasks.auto_cancel_stuck_pending_orders")


@celery_app.task(base=TenantTask)
def fanout_send_checkout_abandonment_emails():
    return run_for_all_tenants("order.tasks.send_checkout_abandonment_emails")


@celery_app.task(base=TenantTask)
def fanout_check_low_stock_products():
    return run_for_all_tenants("product.tasks.check_low_stock_products")
