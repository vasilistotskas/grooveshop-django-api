"""The scheduled gift-card sweep must dispatch through Celery, not eagerly.

Regression cover for a silent production bug: the daily sweep called
``deliver_gift_card_email.apply(args=[card.id])``. ``.apply()`` is
Celery's EAGER path — it never carries the ``_schema_name`` header that
``TenantTask.apply_async`` stamps, so the delivery body executed under
``schema_context("public")`` where the ``giftcard`` table does not
exist. With ``CELERY_TASK_EAGER_PROPAGATES=False`` in production the
resulting exception was swallowed into an ``EagerResult`` while the
sweep still counted the card as sent, so future-dated gift cards were
never delivered and never retried (``delivered_at`` stayed NULL, so the
same rows failed again every day).

Immediate delivery is skipped for scheduled cards
(``giftcard/services.py``), making this sweep their only delivery path.

The suite runs with multi-tenancy disabled (``tests/conftest.py``), so
the schema rebinding itself cannot be exercised here — assert the
dispatch mechanism instead, which is what actually regressed.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from djmoney.money import Money

from giftcard.enum import GiftCardStatus
from giftcard.models import GiftCard
from giftcard.tasks import deliver_scheduled_gift_cards


@pytest.fixture
def due_card(db):
    return GiftCard.objects.create(
        code="TESTSCHEDULED0001",
        initial_value=Money(Decimal("50.00"), "EUR"),
        status=GiftCardStatus.ACTIVE,
        recipient_email="recipient@example.com",
        deliver_at=timezone.now() - timedelta(hours=1),
    )


@pytest.mark.django_db
def test_sweep_dispatches_via_delay_not_apply(due_card):
    """``.delay`` keeps the tenant-schema header; ``.apply`` drops it."""
    with patch("giftcard.tasks.deliver_gift_card_email") as task:
        result = deliver_scheduled_gift_cards()

    task.delay.assert_called_once_with(due_card.id)
    task.apply.assert_not_called()
    assert result == {"status": "success", "sent": 1}


@pytest.mark.django_db
def test_sweep_skips_cards_not_yet_due(due_card):
    due_card.deliver_at = timezone.now() + timedelta(days=2)
    due_card.save(update_fields=["deliver_at"])

    with patch("giftcard.tasks.deliver_gift_card_email") as task:
        result = deliver_scheduled_gift_cards()

    task.delay.assert_not_called()
    assert result == {"status": "success", "sent": 0}


@pytest.mark.django_db
def test_sweep_skips_already_delivered_cards(due_card):
    due_card.delivered_at = timezone.now()
    due_card.save(update_fields=["delivered_at"])

    with patch("giftcard.tasks.deliver_gift_card_email") as task:
        result = deliver_scheduled_gift_cards()

    task.delay.assert_not_called()
    assert result == {"status": "success", "sent": 0}
