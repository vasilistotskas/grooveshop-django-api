"""The platform destroy API never puts exception text in its body."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import ValidationError

from tenant.views import TenantAdminViewSet

LEAK = "Refusing to destroy 'secret_schema': internal detail"


@pytest.mark.parametrize(
    ("refusal", "code"),
    [
        ("protected", "tenant_destroy_protected"),
        ("not_suspended", "tenant_destroy_not_suspended"),
        ("cooldown", "tenant_destroy_cooldown"),
        (None, "tenant_destroy_invalid"),
    ],
)
def test_a_refused_destroy_carries_a_code_and_no_exception_text(refusal, code):
    view = TenantAdminViewSet()
    view.request = MagicMock()
    view._require_public_schema = MagicMock()
    view.get_object = MagicMock()

    with (
        patch("tenant.views.destroy_tenant", side_effect=ValueError(LEAK)),
        patch("tenant.views.destroy_refusal", return_value=refusal),
        pytest.raises(ValidationError) as caught,
    ):
        view.destroy(view.request)

    assert caught.value.detail["detail"].code == code
    assert "secret_schema" not in str(caught.value.detail)
    assert "Refusing" not in str(caught.value.detail)
