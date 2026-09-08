"""``TenantCommandMixin`` must enter ``tenant_context``, not ``schema_context``.

Both django-tenants context managers switch the connection's search path,
so an ORM-only command cannot tell them apart. Only ``tenant_context``
puts the real ``Tenant`` row on ``connection.tenant`` — ``schema_context``
installs a ``FakeTenant`` carrying nothing but a schema name — and every
per-tenant credential is read off exactly that attribute
(``tenant/credentials.py:_get_tenant_field``).

That difference is invisible until a command needs a credential.
``reconcile_acs_cod`` shipped this way and was unusable in production:
``AcsClient.__init__`` raised ``AcsConfigError`` for every tenant because
``acs_credentials()`` returned empty strings, while the nine sibling
commands sharing the same copy-pasted loop kept working. Verified against
production 2026-09-08 — ``schema_context('webside')`` gave
``is_configured=False``, ``tenant_context(tenant)`` gave ``True``.

Remove the ``tenant_context`` call from the mixin and these tests fail.
"""

from dataclasses import dataclass
from unittest.mock import patch

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context, tenant_context

from core.management.tenant_mixin import TenantCommandMixin


@dataclass
class _Tenant:
    """Enough of a ``Tenant`` row to be recognised by identity."""

    schema_name: str
    acs_api_key: str = "live-key"


class _Command(TenantCommandMixin, BaseCommand):
    pass


def _contexts_for(targets):
    command = _Command()
    with patch.object(_Command, "get_target_tenants", return_value=targets):
        return list(command.iter_tenant_contexts({}))


def test_a_targeted_tenant_yields_tenant_context_carrying_the_row():
    tenant = _Tenant("webside")

    (context,) = _contexts_for([tenant])

    assert isinstance(context, tenant_context), (
        "schema_context would strand connection.tenant as a FakeTenant, so "
        "every per-tenant credential would read empty"
    )
    assert not isinstance(context, schema_context)
    assert context.tenant is tenant


def test_entering_the_context_hands_the_row_to_the_connection():
    """The row must reach ``connection.set_tenant``, not just the wrapper.

    This is the assertion that actually pins the credential behaviour:
    ``_get_tenant_field`` does ``getattr(connection.tenant, name)``, so a
    ``set_schema(name)`` call in its place returns ``""`` for every field.
    """
    tenant = _Tenant("webside")
    (context,) = _contexts_for([tenant])

    with patch("django.db.connection.set_tenant") as set_tenant:
        with context:
            pass

    # Two calls by design: the row on entry, the previous tenant on exit.
    entered = set_tenant.call_args_list[0].args[0]
    assert entered is tenant
    assert entered.acs_api_key == "live-key", (
        "the credential fields _get_tenant_field reads must be reachable"
    )
    assert len(set_tenant.call_args_list) == 2, (
        "the previous tenant is restored"
    )


def test_no_target_leaves_the_callers_context_untouched():
    """``[None]`` means the caller already established the context.

    The Celery fanout tasks ``call_command`` from inside ``TenantTask``;
    re-entering would be redundant, and entering the PUBLIC schema would
    be wrong.
    """
    (context,) = _contexts_for([None])

    with patch("django.db.connection.set_tenant") as set_tenant:
        with context:
            pass

    set_tenant.assert_not_called()


def test_every_targeted_tenant_gets_its_own_context():
    """One generator, one context per tenant — no reuse.

    ``tenant_context`` is a ``ContextDecorator``; handing the same
    instance to two ``with`` blocks would nest the restore state.
    """
    first, second = _Tenant("alpha"), _Tenant("beta")

    contexts = _contexts_for([first, second])

    assert [c.tenant for c in contexts] == [first, second]
    assert contexts[0] is not contexts[1]
