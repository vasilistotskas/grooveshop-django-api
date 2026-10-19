"""A review the owner changes goes back to approval, and staff hear of it.

The rules under test:

* the owner changing the rate or the comment of an approved or rejected
  review sends it back to ``NEW`` (hidden, out of the averages); an
  update that changes nothing, and a staff edit, leave it alone;
* ``is_published`` / ``published_at`` follow ``status`` on every path
  that changes it (instance save, bulk moderate, the admin);
* one operational email per entry into ``NEW`` (create and the reset),
  after commit, behind ``OPS_ALERT_REVIEW_PENDING_ENABLED``.
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation
from rest_framework import status
from rest_framework.test import APIClient

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product
from product.models.review import ProductReview
from product.tasks import send_review_pending_alert
from shipping.alerts import OpsAlertKind
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

DEFAULT_LANGUAGE = settings.PARLER_DEFAULT_LANGUAGE_CODE
LANGUAGES = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
OPERATORS = "ops@example.com"


@pytest.fixture(autouse=True)
def _operators(settings):
    settings.ADMINS = [OPERATORS]


@pytest.fixture
def owner():
    return UserAccountFactory(
        num_addresses=0,
        is_superuser=False,
        first_name="Maria",
        last_name="Papadopoulou",
    )


@pytest.fixture
def product():
    return ProductFactory(num_images=0, num_reviews=0)


@pytest.fixture
def staff_api():
    from tenant.models import TenantMembershipRole
    from tests.utils.staff import (
        bind_store_tenant,
        store_staff,
        store_tenant,
        unbind_store_tenant,
    )

    tenant = store_tenant("review_staff")
    previous = bind_store_tenant(tenant)
    try:
        client = APIClient()
        client.force_authenticate(
            user=store_staff(tenant, role=TenantMembershipRole.STAFF)
        )
        yield client
    finally:
        unbind_store_tenant(previous)


@pytest.fixture
def api(owner):
    client = APIClient()
    client.force_authenticate(user=owner)
    return client


def _review(owner, product, review_status, rate=6, comment="Fine"):
    review = ProductReviewFactory(
        product=product,
        user=owner,
        rate=rate,
        status=review_status,
        translations=[],
    )
    for language in LANGUAGES:
        review.set_current_language(language)
        review.comment = comment
    review.save()
    return review


def _patch(client, review, **payload):
    return client.patch(
        reverse("product-review-detail", args=[review.pk]),
        data=payload,
        format="json",
    )


def _comment(text):
    return {"translations": {DEFAULT_LANGUAGE: {"comment": text}}}


def _stored(review):
    return ProductReview.objects.get(pk=review.pk)


class TestOwnerEditReturnsToApproval:
    @pytest.mark.parametrize("before", [ReviewStatus.TRUE, ReviewStatus.FALSE])
    def test_a_new_rate_sends_it_back_to_new(self, api, owner, product, before):
        review = _review(owner, product, before, rate=6)

        response = _patch(api, review, rate=4)

        assert response.status_code == status.HTTP_200_OK
        stored = _stored(review)
        assert stored.rate == 4
        assert stored.status == ReviewStatus.NEW
        assert stored.is_published is False
        assert stored.published_at is None

    @pytest.mark.parametrize("before", [ReviewStatus.TRUE, ReviewStatus.FALSE])
    def test_a_new_comment_sends_it_back_to_new(
        self, api, owner, product, before
    ):
        review = _review(owner, product, before, comment="Fine")

        response = _patch(api, review, **_comment("Actually broken"))

        assert response.status_code == status.HTTP_200_OK
        assert _stored(review).status == ReviewStatus.NEW

    @pytest.mark.parametrize("before", [ReviewStatus.TRUE, ReviewStatus.FALSE])
    def test_an_update_that_changes_nothing_keeps_the_status(
        self, api, owner, product, before
    ):
        review = _review(owner, product, before, rate=6, comment="Fine")
        published_at = _stored(review).published_at

        response = _patch(api, review, rate=6, **_comment("Fine"))

        assert response.status_code == status.HTTP_200_OK
        stored = _stored(review)
        assert stored.status == before
        assert stored.published_at == published_at

    def test_an_empty_comment_on_a_review_without_one_changes_nothing(
        self, api, owner, product
    ):
        review = _review(owner, product, ReviewStatus.TRUE, comment=None)

        _patch(api, review, **_comment(""))

        assert _stored(review).status == ReviewStatus.TRUE

    def test_a_full_put_with_the_same_content_keeps_the_status(
        self, api, owner, product
    ):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)

        response = api.put(
            reverse("product-review-detail", args=[review.pk]),
            data={
                "product": product.pk,
                "rate": 6,
                **_comment("Fine"),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert _stored(review).status == ReviewStatus.TRUE

    def test_the_status_cannot_be_set_by_the_owner(self, api, owner, product):
        review = _review(owner, product, ReviewStatus.NEW)

        _patch(api, review, status="TRUE", is_published=True)

        stored = _stored(review)
        assert stored.status == ReviewStatus.NEW
        assert stored.is_published is False

    def test_a_staff_member_editing_the_review_does_not_reset_it(
        self, staff_api, owner, product
    ):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)
        response = _patch(staff_api, review, rate=2)

        assert response.status_code == status.HTTP_200_OK
        stored = _stored(review)
        assert stored.rate == 2
        assert stored.status == ReviewStatus.TRUE


class TestAReturnedReviewLeavesThePublicSurfaces:
    def test_it_is_off_the_public_list_and_the_averages(
        self, api, owner, product
    ):
        keeper = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.TRUE, 10
        )
        edited = _review(owner, product, ReviewStatus.TRUE, rate=2)
        public = APIClient()

        def public_ids():
            response = public.get(
                reverse("product-review-list"), {"product": product.pk}
            )
            assert response.status_code == status.HTTP_200_OK
            return {row["id"] for row in response.data["results"]}

        def aggregates():
            annotated = (
                Product.objects.with_review_average()
                .with_reviews_count()
                .get(pk=product.pk)
            )
            return annotated.review_average, annotated.reviews_count

        assert public_ids() == {keeper.pk, edited.pk}
        assert aggregates() == (6.0, 2)

        _patch(api, edited, rate=4)

        assert public_ids() == {keeper.pk}
        assert aggregates() == (10.0, 1)
        # The author still sees it, as "in review".
        assert _stored(edited) in ProductReview.objects.visible_to(owner)
        assert _stored(edited) not in ProductReview.objects.visible_to(
            AnonymousUser()
        )


class TestThePublishDateFollowsTheStatus:
    def test_approving_an_instance_publishes_it(self, owner, product):
        review = _review(owner, product, ReviewStatus.NEW)
        assert review.published_at is None

        review.status = ReviewStatus.TRUE
        review.save()

        stored = _stored(review)
        assert stored.is_published is True
        assert stored.published_at is not None

    def test_rejecting_an_instance_clears_it(self, owner, product):
        review = _review(owner, product, ReviewStatus.TRUE)
        assert review.published_at is not None

        review.status = ReviewStatus.FALSE
        review.save()

        stored = _stored(review)
        assert stored.is_published is False
        assert stored.published_at is None

    def test_saving_an_approved_review_keeps_its_date(self, owner, product):
        review = _review(owner, product, ReviewStatus.TRUE)
        fixed = timezone.now() - timedelta(days=30)
        ProductReview.objects.filter(pk=review.pk).update(published_at=fixed)

        review = _stored(review)
        review.rate = 3
        review.save()

        assert _stored(review).published_at == fixed

    def test_a_save_limited_to_status_still_moves_the_date(
        self, owner, product
    ):
        review = _review(owner, product, ReviewStatus.NEW)

        review.status = ReviewStatus.TRUE
        review.save(update_fields=["status"])

        assert _stored(review).published_at is not None

    def test_bulk_approve_stamps_only_what_has_no_date(self, product):
        fresh = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.NEW
        )
        old = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.TRUE
        )
        fixed = timezone.now() - timedelta(days=30)
        ProductReview.objects.filter(pk=old.pk).update(published_at=fixed)

        updated = ProductReview.objects.filter(
            pk__in=[fresh.pk, old.pk]
        ).moderate(ReviewStatus.TRUE)

        # Only the row that changed is counted (and saved).
        assert updated == 1
        assert _stored(fresh).published_at is not None
        assert _stored(fresh).is_published is True
        assert _stored(old).published_at == fixed

    def test_bulk_reject_clears_the_date(self, owner, product):
        review = _review(owner, product, ReviewStatus.TRUE)

        ProductReview.objects.filter(pk=review.pk).moderate(ReviewStatus.FALSE)

        stored = _stored(review)
        assert stored.status == ReviewStatus.FALSE
        assert stored.is_published is False
        assert stored.published_at is None

    def test_the_reset_clears_the_date(self, api, owner, product):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)

        _patch(api, review, rate=5)

        assert _stored(review).published_at is None


class TestTheBackfill:
    @staticmethod
    def _run():
        module = importlib.import_module(
            "product.migrations.0052_backfill_review_published_at"
        )
        module.backfill_published_at(apps, None)

    @staticmethod
    def _undated(review):
        ProductReview.objects.filter(pk=review.pk).update(
            published_at=None, is_published=False
        )

    def test_an_approved_review_gets_its_created_date(self, owner, product):
        review = _review(owner, product, ReviewStatus.TRUE)
        self._undated(review)

        self._run()

        stored = _stored(review)
        assert stored.published_at == stored.created_at
        assert stored.is_published is True

    def test_nothing_else_is_touched_and_a_replay_adds_nothing(self, product):
        pending = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.NEW
        )
        rejected = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.FALSE
        )
        dated = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.TRUE
        )
        fixed = timezone.now() - timedelta(days=30)
        ProductReview.objects.filter(pk=dated.pk).update(published_at=fixed)

        self._run()
        self._run()

        assert _stored(pending).published_at is None
        assert _stored(rejected).published_at is None
        assert _stored(dated).published_at == fixed


def _pending_mails():
    # The orders the setup places mail their customers too.
    return [m for m in mail.outbox if m.to == [OPERATORS]]


class TestTheStaffEmail:
    def test_creating_a_review_emails_the_operators_once(
        self, api, owner, product
    ):
        order = OrderFactory(
            user=owner, status=OrderStatus.COMPLETED, num_order_items=0
        )
        OrderItemFactory(order=order, product=product, quantity=1)

        response = api.post(
            reverse("product-review-list"),
            data={
                "product": product.pk,
                "rate": 9,
                **_comment("Loved it"),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        [message] = _pending_mails()
        assert message.to == [OPERATORS]

    def test_the_content(self, owner, product):
        review = _review(
            owner,
            product,
            ReviewStatus.NEW,
            rate=9,
            comment="Loved it & would buy again",
        )

        result = send_review_pending_alert.run(review.pk)

        assert result == {"status": "sent", "review_id": review.pk}
        [message] = mail.outbox
        name = product.safe_translation_getter("name", any_language=True)
        html = message.alternatives[0][0]
        # The store's language, not the test's: the task renders in it.
        with translation.override(settings.LANGUAGE_CODE):
            admin_path = reverse(
                "admin:product_productreview_change", args=[review.pk]
            )
        assert name in message.subject
        for body in (message.body, html):
            assert name in body
            assert "4.5/5" in body
            assert "Maria P." in body
            assert admin_path in body
            assert owner.email not in body
        # Plain text is not entity-encoded; HTML is escaped.
        assert "Loved it & would buy again" in message.body
        assert "Loved it &amp; would buy again" in html

    def test_a_long_comment_is_cut(self, owner, product):
        review = _review(
            owner, product, ReviewStatus.NEW, comment="word " * 400
        )

        send_review_pending_alert.run(review.pk)

        assert len(mail.outbox[0].body) < 1500

    def test_an_owner_edit_of_an_approved_review_emails_once(
        self, api, owner, product
    ):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)

        _patch(api, review, rate=5)
        _patch(api, review, rate=4)  # already NEW: not another entry

        assert len(_pending_mails()) == 1

    def test_an_edit_that_changes_nothing_sends_nothing(
        self, api, owner, product
    ):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)

        _patch(api, review, rate=6)

        assert mail.outbox == []

    def test_a_staff_edit_sends_nothing(self, staff_api, owner, product):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)
        _patch(staff_api, review, rate=2)

        assert mail.outbox == []

    def test_a_muted_store_sends_nothing(self, api, owner, product):
        switch_store_settings(
            **{OpsAlertKind.REVIEW_PENDING.setting_name: False}
        )
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)

        _patch(api, review, rate=5)

        assert mail.outbox == []
        assert _stored(review).status == ReviewStatus.NEW

    def test_a_review_moderated_before_the_task_ran_is_skipped(
        self, owner, product
    ):
        review = _review(owner, product, ReviewStatus.TRUE)

        result = send_review_pending_alert.run(review.pk)

        assert result == {"status": "skipped", "reason": "not_waiting"}
        assert mail.outbox == []

    def test_the_mail_waits_for_the_commit(self, api, owner, product):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)

        with (
            patch("tenant.celery.transaction.on_commit") as on_commit,
            patch.object(send_review_pending_alert, "apply_async") as queue,
        ):
            _patch(api, review, rate=5)

            [dispatch] = [
                call.args[0]
                for call in on_commit.call_args_list
                if call.kwargs.get("robust")
            ]
            queue.assert_not_called()

            dispatch()

            queue.assert_called_once()
            assert queue.call_args.kwargs["kwargs"] == {"review_id": review.pk}


class TestTheAdmin:
    @pytest.fixture
    def staff_client(self):
        client = Client()
        client.force_login(
            UserAccountFactory(admin=True),
            backend="tenant.auth_backends.PlatformStaffBackend",
        )
        return client

    def _listed(self, client, **query):
        response = client.get(
            reverse("admin:product_productreview_changelist"), query
        )
        assert response.status_code == 200
        return {review.pk for review in response.context["cl"].queryset}

    def test_every_status_costs_the_same_for_more_rows(self, staff_client):
        # The growth guard renders the default (waiting) list; this
        # renders all three statuses, thumbnails and verified purchases.
        url = reverse("admin:product_productreview_changelist")

        def add(count):
            for index in range(count):
                _review(
                    UserAccountFactory(num_addresses=0),
                    ProductFactory(num_reviews=0),
                    list(ReviewStatus)[index % 3],
                )

        def measure():
            staff_client.get(url, {"status__exact": "all"})
            with CaptureQueriesContext(connection) as queries:
                response = staff_client.get(url, {"status__exact": "all"})
            assert response.status_code == 200
            return len(queries.captured_queries)

        add(3)
        few = measure()
        add(6)

        assert measure() == few

    def test_the_changelist_opens_on_the_reviews_waiting(
        self, staff_client, product
    ):
        waiting = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.NEW
        )
        approved = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.TRUE
        )
        rejected = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.FALSE
        )

        assert self._listed(staff_client) == {waiting.pk}
        # The dashboard link keeps its parameter.
        assert self._listed(staff_client, status__exact="NEW") == {waiting.pk}
        assert self._listed(staff_client, status__exact="TRUE") == {approved.pk}
        assert self._listed(staff_client, status__exact="FALSE") == {
            rejected.pk
        }
        assert self._listed(staff_client, status__exact="all") == {
            waiting.pk,
            approved.pk,
            rejected.pk,
        }

    def test_the_filter_is_labelled_for_what_it_shows(
        self, staff_client, product
    ):
        response = staff_client.get(
            reverse("admin:product_productreview_changelist")
        )

        assert "Waiting for approval" in response.content.decode()

    def test_the_status_badge_is_coloured(self):
        from admin.displays import REVIEW_STATUS_VARIANT

        assert REVIEW_STATUS_VARIANT[ReviewStatus.NEW] == "info"
        assert REVIEW_STATUS_VARIANT[ReviewStatus.TRUE] == "success"
        assert REVIEW_STATUS_VARIANT[ReviewStatus.FALSE] == "danger"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("approve_review", ReviewStatus.TRUE),
            ("reject_review", ReviewStatus.FALSE),
        ],
    )
    def test_detail_actions_moderate_and_return_a_response(
        self, staff_client, owner, product, name, expected
    ):
        review = _review(owner, product, ReviewStatus.NEW)
        url = reverse("admin:product_productreview_" + name, args=[review.pk])

        response = staff_client.get(url)

        assert response.status_code == 302
        assert response.url == reverse(
            "admin:product_productreview_change", args=[review.pk]
        )
        stored = _stored(review)
        assert stored.status == expected
        assert (stored.published_at is not None) is (
            expected == ReviewStatus.TRUE
        )

    def test_the_actions_are_on_both_the_list_and_the_change_page(self):
        from django.contrib.admin.sites import site

        model_admin = site._registry[ProductReview]

        assert {"approve_reviews", "reject_reviews"} <= set(model_admin.actions)
        assert {"approve_review", "reject_review"} <= set(
            model_admin.actions_detail
        )

    def test_the_list_actions_publish_and_unpublish(
        self, staff_client, product
    ):
        approve = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.NEW
        )
        reject = _review(
            UserAccountFactory(num_addresses=0), product, ReviewStatus.NEW
        )
        url = reverse("admin:product_productreview_changelist")

        staff_client.post(
            url,
            {"action": "approve_reviews", "_selected_action": [approve.pk]},
        )
        staff_client.post(
            url,
            {"action": "reject_reviews", "_selected_action": [reject.pk]},
        )

        assert _stored(approve).status == ReviewStatus.TRUE
        assert _stored(approve).published_at is not None
        assert _stored(approve).is_published is True
        assert _stored(reject).status == ReviewStatus.FALSE
        assert _stored(reject).published_at is None

    def test_a_staff_edit_in_the_change_form_does_not_reset(
        self, staff_client, owner, product
    ):
        review = _review(owner, product, ReviewStatus.TRUE, rate=6)
        published_at = _stored(review).published_at
        url = reverse("admin:product_productreview_change", args=[review.pk])

        response = staff_client.post(
            f"{url}?language={DEFAULT_LANGUAGE}",
            {
                "product": product.pk,
                "user": owner.pk,
                "rate": 2,
                "status": ReviewStatus.TRUE,
                "comment": "Edited by staff",
            },
        )

        assert response.status_code == 302, response.context["adminform"].errors
        stored = _stored(review)
        assert stored.rate == 2
        assert stored.status == ReviewStatus.TRUE
        assert stored.published_at == published_at
        assert mail.outbox == []

    def test_approving_in_the_change_form_publishes(
        self, staff_client, owner, product
    ):
        review = _review(owner, product, ReviewStatus.NEW, rate=6)
        url = reverse("admin:product_productreview_change", args=[review.pk])

        response = staff_client.post(
            f"{url}?language={DEFAULT_LANGUAGE}",
            {
                "product": product.pk,
                "user": owner.pk,
                "rate": 6,
                "status": ReviewStatus.TRUE,
                "comment": "Fine",
            },
        )

        assert response.status_code == 302, response.context["adminform"].errors
        stored = _stored(review)
        assert stored.is_published is True
        assert stored.published_at is not None


@pytest.mark.django_db
def test_the_reviewer_label_never_shows_the_email_address():
    user = UserAccountFactory(
        first_name="", last_name="", username="", email="maria@example.com"
    )
    review = ProductReviewFactory(user=user)

    assert "maria@example.com" not in review.reviewer_label
