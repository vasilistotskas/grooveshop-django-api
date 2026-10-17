"""The platform's Task Results page says which store a failure belongs to.

Every store's failed background tasks share one public-schema table, so
the page is the platform's, never a store's. The store a row belongs to
is what ``core.result_backend`` recorded in ``TaskResult.meta``.
"""

from __future__ import annotations

import uuid

import pytest
from celery.app.task import Context
from django.test import RequestFactory
from django_celery_results.models import TaskResult
from django_tenants.utils import get_public_schema_name

from admin.platform_site import platform_admin_site
from core.admin import TaskResultStoreFilter
from core.celery import celery_app
from core.result_backend import SCHEMA_META_KEY, store_of
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


class _Schema:
    def __init__(self, schema_name: str) -> None:
        self.schema_name = schema_name


def _store_failure(schema_name: str | None) -> str:
    """Store a FAILURE row the way the backend does for a worker's task."""
    task_id = str(uuid.uuid4())
    headers = {"id": task_id, "task": "tests.task_result_admin"}
    if schema_name is not None:
        headers["_schema_name"] = schema_name
    celery_app.backend.mark_as_failure(
        task_id, RuntimeError("boom"), request=Context(headers)
    )
    return task_id


@pytest.fixture
def stores():
    return store_tenant("alpha_store", name="Alpha Shop"), store_tenant(
        "beta_store", name="Beta Shop"
    )


@pytest.fixture
def rows(stores):
    return {
        "alpha": _store_failure("alpha_store"),
        "beta": _store_failure("beta_store"),
        "platform": _store_failure(get_public_schema_name()),
        "unknown": _store_failure(None),
    }


def _changelist(params: str = ""):
    model_admin = platform_admin_site._registry[TaskResult]
    request = RequestFactory().get(f"/admin/django_celery_results/{params}")
    request.tenant = _Schema(get_public_schema_name())
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)
    response = model_admin.changelist_view(request)
    response.render()
    changelist = response.context_data["cl"]
    return {row.task_id: row for row in changelist.result_list}, response


def test_each_row_is_stamped_with_its_store(rows):
    result, _ = _changelist()

    assert {
        key: store_of(result[task_id]) for key, task_id in rows.items()
    } == {
        "alpha": "alpha_store",
        "beta": "beta_store",
        "platform": get_public_schema_name(),
        "unknown": None,
    }


def test_the_store_column_shows_the_tenant_name_or_platform(rows):
    result, response = _changelist()
    admin_class = platform_admin_site._registry[TaskResult]

    labels = {
        key: str(admin_class.store(result[task_id]))
        for key, task_id in rows.items()
    }

    assert labels == {
        "alpha": "Alpha Shop",
        "beta": "Beta Shop",
        "platform": "Platform",
        "unknown": "Platform",
    }
    assert "Alpha Shop" in response.content.decode()


def test_the_store_filter_narrows_to_one_store(rows):
    result, _ = _changelist("?store=alpha_store")

    assert set(result) == {rows["alpha"]}


def test_the_platform_filter_keeps_public_and_unattributed_rows(rows):
    result, _ = _changelist("?store=platform")

    assert set(result) == {rows["platform"], rows["unknown"]}


def test_the_filter_offers_every_store_and_the_platform(stores):
    model_admin = platform_admin_site._registry[TaskResult]
    request = RequestFactory().get("/admin/")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)

    lookup = TaskResultStoreFilter(
        request, {}, TaskResult, model_admin
    ).lookups(request, model_admin)

    values = [value for value, _label in lookup]
    assert values[0] == "platform"
    assert {"alpha_store", "beta_store"} <= set(values)
    assert get_public_schema_name() not in values


def test_a_store_host_does_not_serve_the_page(rows):
    """A store owner never sees any store's failures."""
    from django.contrib import admin as django_admin

    model_admin = django_admin.site._registry[TaskResult]
    request = RequestFactory().get("/admin/")
    request.tenant = _Schema("alpha_store")
    request.user = UserAccountFactory(is_staff=True, is_superuser=True)

    assert not model_admin.has_module_permission(request)
    assert not model_admin.has_view_permission(request)


def test_the_marker_matches_what_the_backend_stores(rows):
    """The filter is a substring match on ``meta``; this pins the two
    sides together so a change of JSON formatting cannot silently
    empty the filter."""
    row = TaskResult.objects.get(task_id=rows["alpha"])

    assert f'"{SCHEMA_META_KEY}": "alpha_store"' in row.meta
