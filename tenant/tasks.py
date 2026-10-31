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
)
from core.tasks import MonitoredTask
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
    asked, which decides the sender and the language: the store's own
    default locale on a store host, the project default on the platform
    host. The link points at the host the request came in on.
    """
    from django.contrib.auth.tokens import default_token_generator
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse
    from django.utils import translation
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode
    from django.utils.translation import gettext as _

    from admin.password_reset import StaffPasswordResetForm
    from core.utils.email_context import build_email_context
    from tenant.credentials import tenant_from_email
    from tenant.membership import bound_tenant

    tenant = bound_tenant()
    language = getattr(tenant, "default_locale", None) or settings.LANGUAGE_CODE
    sent = 0
    for user in StaffPasswordResetForm().get_users(email):
        # Everything the recipient reads is built in the store's language,
        # the link included: the admin's URLs carry a language prefix, and
        # the page it opens answers in that language.
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
            context = build_email_context(
                language=language,
                reset_url=f"{'https' if secure else 'http'}://{host}{path}",
                valid_minutes=settings.PASSWORD_RESET_TIMEOUT // 60,
            )
            subject = _("Choose a new password for %(store)s") % {
                "store": context["SITE_NAME"]
            }
            html_body = render_to_string(
                "emails/staff/password_reset.html", context
            )
            text_body = render_to_string(
                "emails/staff/password_reset.txt", context
            )
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=tenant_from_email(),
            to=[user.email],
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
        sent += 1
    return {"sent": sent}
