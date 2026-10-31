"""The per-app retention sweeps and how they are scheduled.

Each sweep deletes rows older than its platform setting; the carrier
checkpoints go only for a shipment in a terminal state. The shared loop,
the store-setting sweeps and the sessions are in ``test_retention.py``.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from djmoney.money import Money

from meta_capi.models import MetaCapiEventLog
from meta_capi.tasks import purge_expired_meta_capi_event_logs
from order.enum.payment_transaction import PaymentFeeSource
from order.models import PendingPaymentFee
from order.tasks import purge_expired_pending_payment_fees
from product.factories.product import ProductFactory
from recommendation.enum import EventKind, StrategyCode, Surface
from recommendation.models import RecommendationEvent
from recommendation.tasks import purge_expired_recommendation_events
from search.models import SearchQuery
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import (
    AcsShipmentFactory,
    AcsTrackingEventFactory,
)
from shipping_acs.models import AcsShipment, AcsTrackingEvent
from shipping_acs.tasks import purge_expired_acs_tracking_events
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import (
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.models import BoxNowParcelEvent
from shipping_boxnow.tasks import purge_expired_boxnow_parcel_events
from tenant import tasks as tenant_tasks

pytestmark = pytest.mark.django_db


def _search_query(text: str) -> SearchQuery:
    return SearchQuery.objects.create(
        query=text,
        content_type="product",
        results_count=0,
        estimated_total_hits=0,
    )


def _backdate(model, pk, days: int) -> None:
    model.objects.filter(pk=pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )


class TestRecommendationEventRetention:
    @pytest.mark.parametrize(("age_days", "kept"), [(91, False), (89, True)])
    def test_boundary(self, age_days, kept):
        event = RecommendationEvent.objects.create(
            kind=EventKind.IMPRESSION,
            surface=Surface.PDP,
            strategy=StrategyCode.CURATED,
            product=ProductFactory(),
            impression_id=uuid.uuid4(),
        )
        _backdate(RecommendationEvent, event.pk, age_days)

        with override_settings(RECOMMENDATION_EVENT_RETENTION_DAYS=90):
            result = purge_expired_recommendation_events()

        assert result == {"deleted": 0 if kept else 1}
        assert RecommendationEvent.objects.filter(pk=event.pk).exists() is kept

    def test_the_period_must_outlast_every_window_events_are_read_over(self):
        # A fresh interpreter: ``settings`` is already imported here.
        env = {**os.environ, "RECOMMENDATION_EVENT_RETENTION_DAYS": "7"}

        run = subprocess.run(
            [sys.executable, "-c", "import settings"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert run.returncode != 0
        assert "ImproperlyConfigured" in run.stderr
        assert "longest window" in run.stderr


class TestPendingPaymentFeeRetention:
    @pytest.mark.parametrize(("age_days", "kept"), [(91, False), (89, True)])
    def test_boundary(self, age_days, kept):
        fee = PendingPaymentFee.objects.create(
            provider="viva_wallet",
            provider_reference=str(uuid.uuid4()),
            fee=Money("0.30", "EUR"),
            fee_source=PaymentFeeSource.VIVA_TRANSACTION_PRICE,
        )
        _backdate(PendingPaymentFee, fee.pk, age_days)

        with override_settings(PENDING_PAYMENT_FEE_RETENTION_DAYS=90):
            result = purge_expired_pending_payment_fees()

        assert result == {"deleted": 0 if kept else 1}
        assert PendingPaymentFee.objects.filter(pk=fee.pk).exists() is kept


class TestMetaCapiEventLogRetention:
    @pytest.mark.parametrize(("age_days", "kept"), [(91, False), (89, True)])
    def test_boundary(self, age_days, kept):
        row = MetaCapiEventLog.objects.create(
            event_name="Purchase", event_id=str(uuid.uuid4())
        )
        _backdate(MetaCapiEventLog, row.pk, age_days)

        with override_settings(META_CAPI_EVENT_LOG_RETENTION_DAYS=90):
            purge_expired_meta_capi_event_logs()

        assert MetaCapiEventLog.objects.filter(pk=row.pk).exists() is kept


class TestCarrierCheckpointRetention:
    two_years_and_a_day = 731
    a_day_short = 729

    @pytest.mark.parametrize(
        ("state", "age_days", "kept"),
        [
            (AcsShipmentState.DELIVERED, 731, False),
            (AcsShipmentState.RETURNED, 731, False),
            (AcsShipmentState.CANCELED, 731, False),
            (AcsShipmentState.LOST, 731, False),
            (AcsShipmentState.DELIVERED, 729, True),
            # A shipment still in flight keeps its whole history.
            (AcsShipmentState.IN_TRANSIT, 731, True),
        ],
    )
    def test_acs(self, state, age_days, kept):
        shipment = AcsShipmentFactory(with_voucher=True, shipment_state=state)
        event = AcsTrackingEventFactory(shipment=shipment)
        _backdate(AcsTrackingEvent, event.pk, age_days)

        with override_settings(CARRIER_TRACKING_EVENT_RETENTION_DAYS=730):
            purge_expired_acs_tracking_events()

        assert AcsTrackingEvent.objects.filter(pk=event.pk).exists() is kept
        # Only the checkpoint goes, never the shipment.
        assert AcsShipment.objects.filter(pk=shipment.pk).exists()

    @pytest.mark.parametrize(
        ("state", "age_days", "kept"),
        [
            (BoxNowParcelState.DELIVERED, 731, False),
            (BoxNowParcelState.EXPIRED, 731, False),
            (BoxNowParcelState.DELIVERED, 729, True),
            (BoxNowParcelState.IN_DEPOT, 731, True),
        ],
    )
    def test_boxnow(self, state, age_days, kept):
        shipment = BoxNowShipmentFactory(with_parcel=True, parcel_state=state)
        event = BoxNowParcelEventFactory(shipment=shipment)
        _backdate(BoxNowParcelEvent, event.pk, age_days)

        with override_settings(CARRIER_TRACKING_EVENT_RETENTION_DAYS=730):
            purge_expired_boxnow_parcel_events()

        assert BoxNowParcelEvent.objects.filter(pk=event.pk).exists() is kept


class TestBlankSearchQueryMigration:
    migration = importlib.import_module(
        "search.migrations.0007_delete_blank_search_queries"
    )

    def test_deletes_blank_rows_and_keeps_real_searches(self):
        blank = _search_query("")
        spaces = _search_query("  ")
        real = _search_query("shoes")

        self.migration.delete_blank_queries(django_apps, None)

        assert list(SearchQuery.objects.values_list("pk", flat=True)) == [
            real.pk
        ]
        assert blank.pk != spaces.pk


# Every sweep, the fanout that reaches it and whether SUSPENDED stores are
# included (a retention promise does not pause with a store's billing).
_FANOUTS = [
    (
        "fanout_purge_expired_acs_tracking_events",
        "shipping_acs.tasks.purge_expired_acs_tracking_events",
        "purge-expired-acs-tracking-events",
    ),
    (
        "fanout_purge_expired_boxnow_parcel_events",
        "shipping_boxnow.tasks.purge_expired_boxnow_parcel_events",
        "purge-expired-boxnow-parcel-events",
    ),
    (
        "fanout_purge_expired_recommendation_events",
        "recommendation.tasks.purge_expired_recommendation_events",
        "purge-expired-recommendation-events",
    ),
    (
        "fanout_purge_expired_pending_payment_fees",
        "order.tasks.purge_expired_pending_payment_fees",
        "purge-expired-pending-payment-fees",
    ),
    (
        "fanout_purge_expired_meta_capi_event_logs",
        "meta_capi.tasks.purge_expired_meta_capi_event_logs",
        "purge-expired-meta-capi-event-logs",
    ),
    (
        "fanout_purge_expired_admin_log_entries",
        "core.tasks.purge_expired_admin_log_entries",
        "purge-expired-admin-log-entries-tenants",
    ),
]


class TestScheduling:
    @pytest.mark.parametrize(("fanout", "task", "entry"), _FANOUTS)
    def test_fanout_reaches_suspended_stores_and_is_scheduled(
        self, fanout, task, entry
    ):
        with patch("tenant.tasks.run_for_all_tenants") as run:
            getattr(tenant_tasks, fanout)()

        run.assert_called_once_with(task, include_suspended=True)
        assert (
            settings.CELERY_BEAT_SCHEDULE[entry]["task"]
            == f"tenant.tasks.{fanout}"
        )

    @pytest.mark.parametrize(
        ("entry", "task"),
        [
            (
                "purge-expired-admin-log-entries",
                "core.tasks.purge_expired_admin_log_entries",
            ),
            (
                "purge-expired-cache-purge-logs",
                "core.tasks.purge_expired_cache_purge_logs",
            ),
        ],
    )
    def test_public_schema_passes_run_once_from_beat(self, entry, task):
        assert settings.CELERY_BEAT_SCHEDULE[entry]["task"] == task
