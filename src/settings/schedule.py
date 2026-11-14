"""The beat schedule (``CELERY_BEAT_SCHEDULE``), which is code.

``docs/scheduler.md``."""

from os import getenv

from celery.schedules import crontab

from settings.env import DEBUG, IS_DOCKER, IS_KUBERNETES

SCHEDULE_PRESETS = {
    # Daily schedules
    "daily_2am": crontab(hour="2", minute="0"),
    "daily_3am": crontab(hour="3", minute="0"),
    "daily_4am": crontab(hour="4", minute="0"),
    "daily_5am": crontab(hour="5", minute="0"),
    "daily_6am": crontab(hour="6", minute="0"),
    "daily_noon": crontab(hour="12", minute="0"),
    # Weekly schedules
    "weekly_sunday_5am": crontab(hour="5", minute="0", day_of_week="0"),
    "weekly_sunday_3am": crontab(hour="3", minute="0", day_of_week="0"),
    "weekly_monday_4am": crontab(hour="4", minute="0", day_of_week="1"),
    # Monthly schedules
    "monthly_first_6am": crontab(hour="6", minute="0", day_of_month="1"),
    "monthly_first_4am": crontab(hour="4", minute="0", day_of_month="1"),
    # Bi-monthly schedules
    "bimonthly_6am": crontab(hour="6", minute="0", day_of_month="1,15"),
    # Frequent schedules
    "every_minute": crontab(minute="*"),
    "every_5_minute": crontab(minute="*/5"),
    "every_30_min": crontab(minute="*/30"),
    "every_hour": crontab(minute="0"),
}

# The nightly pg_dump (and the pruning of its output) is production's
# disaster-recovery copy. An environment that is itself a refreshable,
# anonymised clone of production (staging) sets this to False: it then
# schedules neither entry, and beat deletes the rows the cloned database
# brings along. On by default: production relies on the dump.
DATABASE_BACKUPS_ENABLED = getenv("DATABASE_BACKUPS_ENABLED", "True") == "True"
DATABASE_BACKUP_BEAT_ENTRIES = (
    "scheduled-database-backup",
    "cleanup-old-backups",
)


def hourly_under_debug(schedule):
    """``schedule``, or every hour under DEBUG: a developer's beat runs
    the nightly, weekly and monthly jobs within the hour."""
    return SCHEDULE_PRESETS["every_hour"] if DEBUG else schedule


def get_celery_beat_schedule():
    base_schedule = {
        "monitor-system-health": {
            # Every 30 minutes, not nightly. At daily_5am a dependency
            # could be down for 24 hours before anything looked, and on
            # 2026-09-09 one was: Redis crash-looped and webside.gr
            # served 503s while the only check that would have noticed
            # was waiting for 5am.
            #
            # 30 rather than 5 is a deliberate trade. The task emails
            # on every failed run and has nowhere to persist "already
            # alerted" that survives Redis being down, so the interval
            # IS the throttle: a sustained outage costs two mails an
            # hour instead of twelve. The precursor check inside
            # (Redis persistence status) flips hours before a volume
            # actually fills, so the extra latency is spent on warning
            # time we did not have at all before.
            "task": "core.tasks.monitor_system_health",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["every_30_min"]),
        },
        "scheduled-database-backup": {
            "task": "core.tasks.scheduled_database_backup",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_3am"]),
        },
        "cleanup-old-backups": {
            "task": "core.tasks.cleanup_old_backups",
            "schedule": hourly_under_debug(
                SCHEDULE_PRESETS["weekly_sunday_5am"]
            ),
            "kwargs": {"days": 30, "backup_dir": "backups"},
        },
        "clear-duplicate-history": {
            "task": "tenant.tasks.fanout_clear_duplicate_history",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_5am"]),
        },
        "clear-old-history": {
            "task": "tenant.tasks.fanout_clear_old_history",
            "schedule": hourly_under_debug(
                SCHEDULE_PRESETS["weekly_sunday_3am"]
            ),
        },
        # Runs in PUBLIC — purges expired platform-staff sessions.
        "clear-expired-sessions": {
            "task": "core.tasks.clear_expired_sessions_task",
            "schedule": hourly_under_debug(
                SCHEDULE_PRESETS["weekly_monday_4am"]
            ),
        },
        # Fans the same task into every tenant schema — django_session is
        # per-schema, so the public pass above never touches a store's
        # own customer sessions.
        "clear-expired-sessions-tenants": {
            "task": "tenant.tasks.fanout_clear_expired_sessions",
            "schedule": hourly_under_debug(
                SCHEDULE_PRESETS["weekly_monday_4am"]
            ),
        },
        "update-search-click-scores": {
            # Fanout: SearchClick/Product/BlogPost are per-tenant; beat
            # fires in public.
            "task": "tenant.tasks.fanout_update_click_scores",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_3am"]),
        },
        "reevaluate-price-episodes": {
            # Fanout: Product and its price spells are per-tenant; beat
            # fires in public. The net under the EU Omnibus price
            # records: reconciles every product with its live price.
            "task": "tenant.tasks.fanout_reevaluate_price_episodes",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_3am"]),
        },
        "recompute-recommendation-candidates": {
            # Fanout: RecommendationCandidate is per-tenant; beat fires
            # in public. Rebuilds the precomputed strategies' rows
            # (attributes, co_purchase) for the whole catalogue — a
            # product save rebuilds only its own.
            "task": "tenant.tasks.fanout_recompute_recommendation_candidates",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "learn-recommendation-weights": {
            # Fanout: RecommendationSlot/Event are per-tenant; beat
            # fires in public. Re-weights each learning slot from the
            # attach rates of the trailing window.
            "task": "tenant.tasks.fanout_learn_recommendation_weights",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_5am"]),
        },
        "reindex-offer-window-changes": {
            # Fanout: Promotion/Product are per-tenant; beat fires in
            # public. A promotion window opening or closing saves
            # nothing, so no signal reindexes the products' ``on_offer``
            # flag; this sweep does, per tenant, every 5 minutes.
            "task": "tenant.tasks.fanout_reindex_offer_window_changes",
            "schedule": SCHEDULE_PRESETS["every_5_minute"],
            "options": {"queue": "celery", "expires": 240},
        },
        "release-due-publications": {
            # Fanout: blog posts, content pages and reviews are
            # per-tenant; beat fires in public. A row dated in the future
            # goes live with no save, so no signal purges the surfaces
            # that render it or announces it to IndexNow; this sweep
            # does, per tenant, every minute (core/publication.py).
            "task": "tenant.tasks.fanout_release_due_publications",
            "schedule": SCHEDULE_PRESETS["every_minute"],
            "options": {"queue": "celery", "expires": 50},
        },
        "anonymize-old-search-queries": {
            # Fanout: SearchQuery is per-tenant; beat fires in public.
            # kwargs (days=90) live in the wrapper body, not here.
            "task": "tenant.tasks.fanout_anonymize_old_search_queries",
            "schedule": hourly_under_debug(
                SCHEDULE_PRESETS["weekly_sunday_3am"]
            ),
        },
        "reap-unclaimed-contact-attachments": {
            # Fanout: ContactAttachment is per-tenant; beat fires in
            # public. Uploads whose enquiry was never submitted - a
            # closed tab - are bytes nobody asked us to keep, so this
            # runs hourly rather than nightly.
            "task": "tenant.tasks.fanout_reap_unclaimed_attachments",
            "schedule": SCHEDULE_PRESETS["every_hour"],
        },
        "purge-expired-contact-attachment-files": {
            # Fanout: drops the BYTES of claimed attachments past the
            # store's retention window, keeping the rows.
            "task": "tenant.tasks.fanout_purge_expired_attachment_files",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-expired-acs-api-calls": {
            # Fanout: AcsApiCall is per-tenant. Deletes the ACS call
            # history older than ACS_API_CALL_RETENTION_DAYS.
            "task": "tenant.tasks.fanout_purge_expired_acs_api_calls",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-expired-acs-tracking-events": {
            # Fanout: AcsTrackingEvent is per-tenant. Deletes checkpoints
            # of settled shipments older than
            # CARRIER_TRACKING_EVENT_RETENTION_DAYS.
            "task": "tenant.tasks.fanout_purge_expired_acs_tracking_events",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-expired-boxnow-parcel-events": {
            # Fanout: BoxNowParcelEvent is per-tenant; same period as the
            # ACS checkpoints above.
            "task": "tenant.tasks.fanout_purge_expired_boxnow_parcel_events",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-expired-recommendation-events": {
            # Fanout: RecommendationEvent is per-tenant. Deletes events
            # older than RECOMMENDATION_EVENT_RETENTION_DAYS.
            "task": "tenant.tasks.fanout_purge_expired_recommendation_events",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-expired-pending-payment-fees": {
            # Fanout: PendingPaymentFee is per-tenant. Deletes fees whose
            # payment was never booked, older than
            # PENDING_PAYMENT_FEE_RETENTION_DAYS.
            "task": "tenant.tasks.fanout_purge_expired_pending_payment_fees",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-expired-meta-capi-event-logs": {
            # Fanout: MetaCapiEventLog is per-tenant. Deletes delivery
            # rows older than META_CAPI_EVENT_LOG_RETENTION_DAYS.
            "task": "tenant.tasks.fanout_purge_expired_meta_capi_event_logs",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        # Runs in PUBLIC: the platform staff's own audit trail, under the
        # ADMIN_LOG_RETENTION_DAYS stored in the public schema.
        "purge-expired-admin-log-entries": {
            "task": "core.tasks.purge_expired_admin_log_entries",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        # Runs in PUBLIC: the platform types' EmailLog, under the
        # EMAIL_LOG_RETENTION_DAYS stored in the public schema.
        "purge-expired-email-logs": {
            "task": "emails.tasks.purge_expired_email_logs",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        # Fans the same task into every store: EmailLog is per schema, so
        # the public pass above never reaches a store's rows.
        "purge-expired-email-logs-tenants": {
            "task": "tenant.tasks.fanout_purge_expired_email_logs",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        # Fans the same task into every store: django_admin_log is per
        # schema, so the public pass above never reaches a store's rows.
        "purge-expired-admin-log-entries-tenants": {
            "task": "tenant.tasks.fanout_purge_expired_admin_log_entries",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        # Runs in PUBLIC: CachePurgeLog exists only there (every store's
        # purges, tagged by schema_name).
        "purge-expired-cache-purge-logs": {
            "task": "core.tasks.purge_expired_cache_purge_logs",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "redispatch-deferred-tasks": {
            # Public scope, no fanout: the rows live in ``public``. A task
            # whose publish failed after its change committed (the broker
            # was unreachable) is published again here
            # (``tenant.celery.dispatch_on_commit``).
            "task": "tenant.tasks.redispatch_deferred_tasks",
            "schedule": SCHEDULE_PRESETS["every_minute"],
        },
        "run-due-builder-publishes": {
            # Fanout: a publish scheduled in the page builder goes live
            # within the minute it is due (``builder.scheduling``). Cheap
            # when idle: one indexed query per store.
            "task": "tenant.tasks.fanout_run_due_builder_publishes",
            "schedule": SCHEDULE_PRESETS["every_minute"],
        },
        "purge-expired-builder-preview-links": {
            # Fanout: share-preview links hold frozen drafts; an expired one
            # opens nothing and is removed (``builder.preview_links``).
            "task": "tenant.tasks.fanout_purge_expired_builder_preview_links",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "rescan-pending-contact-attachments": {
            # Fanout: an attachment whose scan was queued by a worker that
            # died before the publish stays PENDING; this finds it by state.
            "task": "tenant.tasks.fanout_rescan_pending_contact_attachments",
            "schedule": SCHEDULE_PRESETS["every_30_min"],
        },
        "purge-expired-contact-records": {
            # Fanout: Contact and Feedback are per-tenant. Deletes the
            # rows (and, through the attachment signal, their files)
            # older than the store's CONTACT_RETENTION_DAYS.
            "task": "tenant.tasks.fanout_purge_expired_contact_records",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-unconfirmed-guest-subscriptions": {
            # Fanout: UserSubscription is per-tenant. A guest newsletter
            # signup never confirmed within 30 days is an address and a
            # consent record nobody gave us permission to keep.
            "task": "tenant.tasks.fanout_purge_unconfirmed_guest_subscriptions",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "purge-unconfirmed-guest-alerts": {
            # Fanout: ProductAlert is per-tenant. A guest's alert never
            # confirmed within its link's week is an address nobody gave
            # us permission to keep.
            "task": "tenant.tasks.fanout_purge_unconfirmed_guest_alerts",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "cleanup-expired-data-exports": {
            # Fanout: UserDataExport is per-tenant; beat fires in public.
            "task": "tenant.tasks.fanout_cleanup_expired_data_exports",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_3am"]),
        },
        "send-inactive-user-notifications": {
            "task": "tenant.tasks.fanout_send_inactive_user_notifications",
            "schedule": hourly_under_debug(
                SCHEDULE_PRESETS["monthly_first_6am"]
            ),
        },
        "clear-all-cache": {
            "task": "core.tasks.clear_all_cache_task",
            # Not ``hourly_under_debug``, on purpose: that would clear
            # the whole cache every hour in development.
            "schedule": SCHEDULE_PRESETS["monthly_first_4am"],
        },
        "reset-demo-stores": {
            # Demo stores hand their login out in public, so they
            # collect a day of visitor orders, reviews, comments and
            # carts. This puts the showroom back. Only tenants flagged
            # is_demo are touched — the fan-out filters on it rather
            # than using run_for_all_tenants, because a reset that ran
            # against a real merchant would delete customer data.
            "task": "tenant.tasks.fanout_reset_demo_stores",
            "schedule": SCHEDULE_PRESETS["daily_4am"],
        },
        "cleanup-abandoned-carts": {
            "task": "tenant.tasks.fanout_cleanup_abandoned_carts",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
        },
        "cleanup-old-guest-carts": {
            "task": "tenant.tasks.fanout_cleanup_old_guest_carts",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_6am"]),
        },
        "clear-expired-notifications": {
            "task": "tenant.tasks.fanout_clear_expired_notifications",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["bimonthly_6am"]),
        },
        "sync-meilisearch-indexes": {
            "task": "tenant.tasks.fanout_sync_meilisearch_indexes",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_2am"]),
        },
        "cleanup-expired-stock-reservations": {
            "task": "tenant.tasks.fanout_cleanup_expired_stock_reservations",
            "schedule": SCHEDULE_PRESETS["every_hour"],
        },
        "purge-expired-tenant-archives": {
            # NOT a fanout: TenantArchive rows live in the public schema
            # precisely because the schemas they describe were dropped.
            # Erases a destroyed store's invoices once the statutory
            # retention period expires — keeping them past that point
            # would breach storage limitation (GDPR art. 5(1)(e)) just
            # as deleting them early would breach the tax-record duty.
            "task": "tenant.tasks.purge_expired_tenant_archives",
            "schedule": SCHEDULE_PRESETS["daily_3am"],
        },
        "process-tenant-billing": {
            # NOT a fanout: billing terms and dunning bookkeeping are
            # public-schema Tenant rows — one pass covers the estate.
            "task": "tenant.tasks.process_tenant_billing",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_6am"]),
        },
        # The scheduled domain tasks below run via tenant fanout
        # wrappers — the beat scheduler dispatches once against public,
        # the fanout helper then re-dispatches the underlying task into
        # every active tenant schema with the right _schema_name header.
        "process-loyalty-points-expiration": {
            "task": "tenant.tasks.fanout_process_points_expiration",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_3am"]),
        },
        "expire-gift-cards": {
            "task": "tenant.tasks.fanout_expire_gift_cards",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_3am"]),
        },
        "deliver-scheduled-gift-cards": {
            # Cards bought with a future deliver_at — a daily sweep
            # instead of far-future ETAs, which brokers redeliver badly.
            "task": "tenant.tasks.fanout_deliver_scheduled_gift_cards",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_6am"]),
        },
        "send-gift-card-expiry-reminders": {
            "task": "tenant.tasks.fanout_send_gift_card_expiry_reminders",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_6am"]),
        },
        "auto-cancel-stuck-pending-orders": {
            "task": "tenant.tasks.fanout_auto_cancel_stuck_pending_orders",
            "schedule": crontab(minute="*/15"),
        },
        # DELIVERED + paid → COMPLETED is applied inline by the carrier
        # poll and the COD reconcile; this sweep closes what lands
        # outside those two moments (a payment entered by hand, a
        # webhook after delivery, an inline attempt that failed once).
        "complete-paid-delivered-orders": {
            "task": "tenant.tasks.fanout_complete_paid_delivered_orders",
            "schedule": SCHEDULE_PRESETS["every_hour"],
        },
        # Provider refunds still PENDING after
        # ``REFUND_RECONCILE_AFTER_MINUTES``: the provider is asked what
        # became of each. A pending attempt holds its money until resolved.
        "reconcile-pending-refunds": {
            "task": "tenant.tasks.fanout_reconcile_pending_refunds",
            "schedule": SCHEDULE_PRESETS["every_hour"],
        },
        # Refunds, attempts and chargeback decisions booked without an
        # allocation (by a release that predates it) are allocated here.
        "allocate-credit-sources": {
            "task": "tenant.tasks.fanout_allocate_credit_sources",
            "schedule": SCHEDULE_PRESETS["every_5_minute"],
        },
        "check-low-stock-products": {
            "task": "tenant.tasks.fanout_check_low_stock_products",
            "schedule": SCHEDULE_PRESETS["every_hour"],
        },
        # The control plane's per-store figures (TenantStatsSnapshot).
        "refresh-tenant-stats": {
            "task": "tenant.tasks.fanout_refresh_stats_snapshots",
            "schedule": SCHEDULE_PRESETS["every_30_min"],
        },
        "send-review-request-emails": {
            "task": "tenant.tasks.fanout_send_review_request_emails",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_noon"]),
        },
        # One IndexNow POST per store per run, with the product and blog
        # pages that changed since the last one. IndexNow asks for at
        # least five minutes between two submissions of the same URL.
        "submit-indexnow-urls": {
            "task": "tenant.tasks.fanout_submit_indexnow_urls",
            "schedule": SCHEDULE_PRESETS["every_30_min"],
        },
        "send-checkout-abandonment-emails": {
            "task": "tenant.tasks.fanout_send_checkout_abandonment_emails",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_6am"]),
        },
        "sync-boxnow-lockers": {
            # Fanout: BoxNowLocker lives in each tenant schema; a direct
            # beat call would sync into the empty public schema. Per-tenant
            # fanout keeps every tenant's locker cache fresh.
            "task": "tenant.tasks.fanout_sync_boxnow_lockers",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_2am"]),
            "options": {"queue": "celery"},
        },
        # Poll BoxNow's REST API every 15 min for non-terminal
        # shipments. Defence-in-depth against a missed webhook
        # delivery: BoxNow's webhook URL is partner-managed by
        # BoxNow support (one URL per partner, no self-serve
        # registration), so a single misconfiguration would freeze
        # parcel state at our last observed value. The poll
        # converges state independent of webhook health.
        # ``expires=300``: a poll still queued after that is overtaken by
        # the next one, so a backlog does not run them all in a row. Only an
        # entry that runs again soon may expire; a daily one would lose its
        # day (tests/integration/scheduler/test_beat.py).
        "poll-boxnow-tracking": {
            # Fanout wrapper, not the task directly: beat fires in the
            # public schema, so calling poll_boxnow_tracking_batch
            # directly would query the empty public-schema
            # BoxNowShipment table. The fanout iterates active tenants
            # and dispatches the batch per-schema. Mirrors
            # poll-acs-tracking. (Regression: main's b32aea39 added this
            # beat task without a tenant fanout.)
            "task": "tenant.tasks.fanout_poll_boxnow_tracking_batch",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "celery", "expires": 300},
        },
        # ACS — daily station sync at 03:00 Athens (Phase 2 only fires
        # when the AcsStation cache is in use; harmless to schedule now
        # since the task no-ops when ACS provider is inactive).
        "sync-acs-stations": {
            # Fanout: AcsStation is per-tenant; beat fires in public.
            "task": "tenant.tasks.fanout_sync_acs_stations",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_3am"]),
            "options": {"queue": "celery"},
        },
        # Issue the daily ACS pickup list Mon-Fri at 16:30 Athens, the
        # same path as the admin's "Issue ACS pickup list now". ACS
        # rejects the WHOLE list while any voucher is unprinted and
        # collects printed parcels without one, so a refusal only leaves
        # an order note (``shipping_acs.tasks``) — nobody is emailed.
        "issue-acs-pickup-list": {
            # Fanout: AcsShipment is per-tenant; a direct beat call would
            # query the empty public schema and never present vouchers to
            # ACS for collection. Per-tenant fanout issues each tenant's
            # daily manifest.
            "task": "tenant.tasks.fanout_issue_daily_acs_pickup_list",
            "schedule": crontab(hour="16", minute="30", day_of_week="mon-fri"),
            "options": {"queue": "celery"},
        },
        # Poll ACS_Trackingsummary every 15 min for non-terminal
        # shipments. Sub-tasks fan out internally spaced by
        # ACS_POLL_DISPATCH_SPACING_SECONDS; the calls in flight are
        # capped by shipping_acs.concurrency. Distributed mutex inside
        # the task body prevents two workers from each running the full
        # fan-out concurrently.
        "poll-acs-tracking": {
            # Beat fires in the public schema. Without the fanout wrapper
            # the worker would query `AcsShipment` in public — where no
            # tenant shipment rows exist — and silently process zero
            # parcels per tick. The fanout iterates active tenants and
            # dispatches per-schema.
            "task": "tenant.tasks.fanout_poll_acs_tracking_batch",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "celery", "expires": 300},
        },
        # Send the voucher requests that waited while ACS was failing
        # (``shipping_acs.voucher_hold``). While a hold is in force the
        # task first asks ACS a read, and then sends one request before
        # the rest. It is the only retry of a held request, so it runs
        # often; with nothing held it is two small queries per store.
        "issue-held-acs-vouchers": {
            # Fanout: AcsShipment and AcsVoucherHold are per-tenant.
            "task": "tenant.tasks.fanout_issue_held_acs_vouchers",
            "schedule": SCHEDULE_PRESETS["every_5_minute"],
            "options": {"queue": "celery", "expires": 240},
        },
        # Surface non-terminal ACS shipments with no tracking movement
        # for ACS_STALE_SHIPMENT_DAYS days (stuck parcels, dead
        # vouchers) to the owning store's operators (the
        # ``ops.stale_shipments`` email). Daily at 09:00 Athens so the
        # digest lands at the start of the working day; the claim flag
        # inside the task dedupes re-alerts.
        "check-stale-acs-shipments": {
            # Fanout: AcsShipment is per-tenant; beat fires in public.
            "task": "tenant.tasks.fanout_check_stale_acs_shipments",
            "schedule": crontab(hour="9", minute="0"),
            "options": {"queue": "celery"},
        },
        # Reconcile yesterday's COD payouts daily at 02:30 Athens —
        # safely after midnight so ACS's books for the prior day are
        # finalised.  The service method is idempotent on
        # (voucher_no, cod_payment_date) so an early run or a re-run
        # is harmless.
        "reconcile-acs-cod-payouts": {
            # Same fanout requirement as poll-acs-tracking — AcsShipment
            # and AcsCodPayout are tenant-scoped tables, so beat must
            # dispatch per-schema or the reconciliation runs against the
            # empty public schema.
            "task": "tenant.tasks.fanout_reconcile_acs_cod_payouts",
            "schedule": hourly_under_debug(crontab(hour="2", minute="30")),
            "options": {"queue": "celery"},
        },
        # Half an hour after the reconcile, so a payout that landed
        # overnight is already recorded and cannot be reported overdue.
        # Once a day is right for money that is already days late —
        # anything more often just repeats the same list.
        "alert-unremitted-acs-cod": {
            "task": "tenant.tasks.fanout_alert_unremitted_cod_payouts",
            "schedule": hourly_under_debug(crontab(hour="3", minute="0")),
            "options": {"queue": "celery"},
        },
    }

    if not DATABASE_BACKUPS_ENABLED:
        for name in DATABASE_BACKUP_BEAT_ENTRIES:
            del base_schedule[name]

    if IS_DOCKER and not IS_KUBERNETES:
        base_schedule["clear-development-logs"] = {
            "task": "core.tasks.clear_development_log_files_task",
            "schedule": hourly_under_debug(SCHEDULE_PRESETS["daily_4am"]),
            "kwargs": {"days": 7},
        }

    return base_schedule


# The whole schedule: ``scheduler.beat`` deletes any other PeriodicTask row
# when beat starts. To change when a job runs, change it here and deploy.
CELERY_BEAT_SCHEDULE = get_celery_beat_schedule()
