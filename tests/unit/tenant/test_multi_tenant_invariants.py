"""Unit-level invariants of the multi-tenant plumbing.

Each test in this module targets one foundational guarantee the rest
of the system depends on. The goal isn't end-to-end coverage — it's
to catch a regression that silently bypasses tenant scoping (the
class of bug MULTI_TENANT_AUDIT.md is built around).

Tests in this file deliberately avoid real schema creation: we use the
``tenant_factory`` / ``bind_tenant`` fixtures from
``tests/unit/tenant/conftest.py`` so the unit suite stays sub-second.
End-to-end isolation (two real schemas, real ORM writes) lives in
``tests/integration/tenant/test_multi_tenant_invariants.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.db import connection

from tenant.celery import TenantTask


class TestTenantTaskCallEntersSchema:
    """``TenantTask.__call__`` must wrap ``super().__call__`` in
    ``schema_context(self.request._schema_name)``. If a regression
    drops that wrapper, every Celery task silently runs in public.

    Exercising ``Task.__call__`` directly requires a full
    Celery-registered task (the request stack is None on a bare
    instance) which the rest of the codebase already covers via
    ``CELERY_TASK_ALWAYS_EAGER=True`` integration paths. The
    unit-level guarantee we encode here is a source-level regression
    check: the wrapper body must contain both ``schema_context`` and
    a ``super().__call__`` delegation. If a contributor deletes
    either, this test fails before the change reaches CI.
    """

    def test_call_body_contains_schema_context_wrapper(self) -> None:
        import inspect

        src = inspect.getsource(TenantTask.__call__)
        assert "schema_context" in src, (
            "TenantTask.__call__ no longer mentions schema_context — "
            "every task would run in public. See MULTI_TENANT_AUDIT.md."
        )
        assert "super().__call__" in src, (
            "TenantTask.__call__ no longer delegates to the parent "
            "Task.__call__; tasks would never actually run."
        )

    def test_call_reads_schema_name_from_request(self) -> None:
        import inspect

        src = inspect.getsource(TenantTask.__call__)
        assert "_schema_name" in src, (
            "TenantTask.__call__ no longer reads the _schema_name "
            "header — apply_async would emit the header but the "
            "worker would ignore it."
        )


class TestTenantTaskApplyAsyncHeader:
    """``apply_async`` must stamp ``_schema_name`` from the explicit
    headers kwarg first, then ``connection.schema_name``. The fallback
    is the bug class C1 / H8 in MULTI_TENANT_AUDIT.md.
    """

    def test_explicit_schema_header_wins_over_connection(self) -> None:
        captured: dict = {}

        class _T(TenantTask):
            name = "tests.unit.tenant.explicit_header_task"

            def run(self, *a, **k):  # pragma: no cover — never executes
                return None

        task = _T()

        def _super_apply_async(*args, **kwargs):
            captured.update(kwargs)
            return MagicMock(id="task-id")

        with patch("celery.Task.apply_async", side_effect=_super_apply_async):
            task.apply_async(headers={"_schema_name": "tenant_beta"})

        assert captured["headers"]["_schema_name"] == "tenant_beta"

    def test_connection_schema_used_when_no_explicit_header(
        self, monkeypatch
    ) -> None:
        captured: dict = {}

        class _T(TenantTask):
            name = "tests.unit.tenant.fallback_header_task"

            def run(self, *a, **k):
                return None

        task = _T()

        # Simulate request thread inside a tenant schema_context.
        monkeypatch.setattr(
            connection, "schema_name", "tenant_gamma", raising=False
        )

        def _super_apply_async(*args, **kwargs):
            captured.update(kwargs)
            return MagicMock(id="task-id")

        with patch("celery.Task.apply_async", side_effect=_super_apply_async):
            task.apply_async()

        assert captured["headers"]["_schema_name"] == "tenant_gamma"


class TestMeiliIndexNamePerTenant:
    """``IndexMixin.get_meili_index_name`` must emit a schema-prefixed
    index name when the connection is in a non-public schema. C6 in
    MULTI_TENANT_AUDIT.md removed the class-load auto-create; this
    test pins down the per-tenant naming contract so the regression
    can't sneak back via ``meilisearch_sync_all_indexes``.
    """

    def test_index_name_is_schema_prefixed_in_tenant_schema(
        self, monkeypatch
    ) -> None:
        from product.models.product import ProductTranslation

        monkeypatch.setattr(
            connection, "schema_name", "tenant_alpha", raising=False
        )
        name = ProductTranslation.get_meili_index_name()
        assert name.startswith("tenant_alpha__"), name

    def test_index_name_is_bare_in_public_schema(self, monkeypatch) -> None:
        from product.models.product import ProductTranslation

        monkeypatch.setattr(connection, "schema_name", "public", raising=False)
        name = ProductTranslation.get_meili_index_name()
        assert "__" not in name, name


class TestEmailHelperCallSites:
    """Audit that the per-tenant email helpers (``tenant_from_email``,
    ``tenant_contact_email``) are actually used wherever the tenant
    fallback matters. A grep-based static check is cheap to maintain
    and catches the regression where a contributor reaches for
    ``settings.DEFAULT_FROM_EMAIL`` directly (H23 / H1 in
    MULTI_TENANT_AUDIT.md).
    """

    REQUIRED_HELPERS = ("tenant_from_email", "tenant_contact_email")

    EXPECTED_CALLERS = (
        # path inside grooveshop-django-api / required helpers
        ("user/utils/subscription.py", REQUIRED_HELPERS),
        ("contact/tasks.py", REQUIRED_HELPERS),
        ("order/tasks.py", REQUIRED_HELPERS),
        ("shipping_acs/tasks.py", REQUIRED_HELPERS),
        ("shipping_boxnow/tasks.py", REQUIRED_HELPERS),
    )

    def test_outbound_email_paths_use_tenant_helpers(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[3]
        for rel_path, helpers in self.EXPECTED_CALLERS:
            target = root / rel_path
            assert target.exists(), f"missing expected file {target}"
            body = target.read_text(encoding="utf-8")
            for helper in helpers:
                assert helper in body, (
                    f"{rel_path} no longer references {helper}() — "
                    "either the file was renamed or a contributor "
                    "switched it back to settings.DEFAULT_FROM_EMAIL"
                )
