"""Every URL-backed unfold action must return an HttpResponse.

``actions_row``, ``actions_list`` and ``actions_detail`` are registered
as real URLs by ``unfold``'s ModelAdmin.get_urls, and its ``@action``
decorator hands the decorated method's return value straight back to
Django. An action that falls off the end returns ``None``, and Django
raises "didn't return an HttpResponse object" — so the button does its
work (dispatching the Celery task) and *then* 500s: the operator sees a
server error with no confirmation, and retries something that already
ran.

``repoll_tracking``, ``issue_voucher_now`` and ``run_reconciliation``
all shipped that way. ``actions_submit_line`` is exempt — it is called
from ``save_model``, not routed as a URL.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.http.response import HttpResponseBase
from django.test import RequestFactory

from shipping_acs.admin import AcsCodPayoutAdmin, AcsShipmentAdmin
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsCodPayout, AcsShipment

pytestmark = pytest.mark.django_db

URL_BACKED = {"actions_row", "actions_list", "actions_detail"}


def _request():
    request = RequestFactory().get("/")
    request.user = get_user_model()(id=1, username="staff")
    return request


class TestRowActionsReturnAResponse:
    def test_repoll_tracking(self):
        admin = AcsShipmentAdmin(AcsShipment, AdminSite())
        shipment = AcsShipmentFactory(voucher_no="9700000101")

        with (
            patch("shipping_acs.tasks.poll_acs_tracking_one.delay") as task,
            patch.object(admin, "message_user"),
            patch("django.contrib.messages.info"),
        ):
            response = admin.repoll_tracking(_request(), shipment.id)

        task.assert_called_once_with(shipment.id)
        assert isinstance(response, HttpResponseBase)
        assert response.status_code == 302

    def test_issue_voucher_now(self):
        admin = AcsShipmentAdmin(AcsShipment, AdminSite())
        shipment = AcsShipmentFactory(
            voucher_no=None, shipment_state=AcsShipmentState.PENDING_CREATION
        )

        with (
            patch(
                "shipping_acs.tasks.create_acs_voucher_for_order.delay"
            ) as task,
            patch("django.contrib.messages.info"),
        ):
            response = admin.issue_voucher_now(_request(), shipment.id)

        task.assert_called_once_with(shipment.order_id)
        assert response.status_code == 302

    def test_issue_voucher_now_when_the_shipment_is_gone(self):
        # The early-exit branch returned a bare ``return`` — the same
        # 500, reached by a different path.
        admin = AcsShipmentAdmin(AcsShipment, AdminSite())

        with patch("django.contrib.messages.error"):
            response = admin.issue_voucher_now(_request(), 10**9)

        assert response.status_code == 302

    def test_run_reconciliation(self):
        admin = AcsCodPayoutAdmin(AcsCodPayout, AdminSite())

        with (
            patch("shipping_acs.tasks.reconcile_acs_cod_payouts.delay") as task,
            patch("django.contrib.messages.info"),
        ):
            response = admin.run_reconciliation(_request(), 1)

        task.assert_called_once_with()
        assert response.status_code == 302
        # Back to ITS own changelist, not the shipment one.
        assert "acscodpayout" in response.url


class TestNoUrlBackedActionCanReturnNone:
    """Source-level invariant, because the failure is a missing return.

    A behavioural test only covers the actions that exist today; this
    catches the next one somebody adds. Asserted against the AST rather
    than by rendering, since reproducing it needs a real admin request
    cycle to raise the ValueError.
    """

    @staticmethod
    def _falls_off_the_end(fn: ast.FunctionDef) -> bool:
        def returns(body) -> bool:
            for stmt in reversed(body):
                if isinstance(stmt, ast.Return):
                    return stmt.value is not None
                if isinstance(stmt, ast.Raise):
                    return True
                if (isinstance(stmt, ast.If) and stmt.orelse) and (
                    returns(stmt.body) and returns(stmt.orelse)
                ):
                    return True
                if (isinstance(stmt, ast.Try)) and (
                    returns(stmt.body)
                    and all(returns(h.body) for h in stmt.handlers)
                ):
                    return True
            return False

        bare = any(
            isinstance(n, ast.Return) and n.value is None for n in ast.walk(fn)
        )
        return bare or not returns(fn.body)

    def _offenders(self, path: pathlib.Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = []
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            names: set[str] = set()
            for stmt in cls.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id in URL_BACKED:
                        names.update(
                            el.value
                            for el in ast.walk(stmt.value)
                            if isinstance(el, ast.Constant)
                            and isinstance(el.value, str)
                        )
            for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
                if fn.name in names and self._falls_off_the_end(fn):
                    found.append(
                        f"{path.name}:{fn.lineno} {cls.name}.{fn.name}"
                    )
        return found

    def test_every_admin_in_the_project(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        admins = [
            p
            for p in root.rglob("admin.py")
            if ".venv" not in p.parts and "site-packages" not in p.parts
        ]
        assert admins, "found no admin.py to check — path assumption broke"

        offenders: list[str] = []
        for path in admins:
            offenders.extend(self._offenders(path))

        assert not offenders, (
            "URL-backed unfold actions that can return None (Django will "
            f"raise 'didn't return an HttpResponse'): {offenders}"
        )
