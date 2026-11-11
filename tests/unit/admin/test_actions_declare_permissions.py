"""Every admin action names the permission it needs, in its decorator.

Django and Unfold both FAIL OPEN on an action that declares nothing.
Django's ``_filter_actions_by_permissions`` keeps it, so a bulk action is
offered to anyone who opens the changelist. Unfold is worse for
``actions_list``, ``actions_row`` and ``actions_detail``: each is a URL
of its own (``unfold.admin.ModelAdmin.get_urls``) wrapped only in
``admin_site.admin_view``, and the one check behind it is the
``permissions`` the decorator was given (``unfold.decorators.action``:
``if permissions:``). With none, any signed-in member of the store's
staff runs the action by its URL, on a page they may not even open: a
store's STAFF member started the ACS COD reconciliation of a
superuser-only ledger that way (#646).

``permissions`` has to be in the decorator. Unfold reads the argument it
closed over, never ``allowed_permissions`` on the function, so setting
that attribute afterwards (as ``BaseModelAdmin`` did until #646) hid the
button and left the URL open.

Two guards. The first walks the source of every module by ``ast``: a
function decorated as an admin action, or a method a class lists in an
action slot, must pass a literal ``permissions=[...]``. The second walks
the admin registry, which also sees an action a class only inherits.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib import admin as django_admin

from admin.base import BaseModelAdmin

ACTION_SLOTS = (
    "actions",
    "actions_list",
    "actions_detail",
    "actions_row",
    "actions_submit_line",
)

# Where an admin action decorator comes from: module -> its name there.
_DECORATOR_MODULES = {
    "unfold.decorators": "action",
    "django.contrib.admin": "action",
    "django.contrib.admin.decorators": "action",
}
# ``from django.contrib import admin`` -> ``admin.action``.
_DECORATOR_PACKAGES = {
    ("django.contrib", "admin"),
    ("unfold", "decorators"),
}

# The actions that only READ, each with why: the only ones that may ask
# for ``view`` alone. An action that changes a row, sends something or
# asks a carrier to act asks for ``change`` (``add`` for a copy,
# ``delete`` for a deletion), so it cannot be declared out of the way
# with ``permissions=["view"]``.
READ_ONLY: dict[str, str] = {
    "src/admin/export.py::ExportActionMixin.export_csv": (
        "streams the selected rows as CSV"
    ),
    "src/admin/export.py::ExportActionMixin.export_xml": (
        "streams the selected rows as XML"
    ),
    "src/order/admin.py::OrderAdmin.view_customer_orders_row": (
        "a redirect to the order list filtered by the row's customer"
    ),
    "src/shipping_boxnow/admin.py::BoxNowShipmentAdmin.resync_label": (
        "fetches the labels from BoxNow into the cache; no row and no "
        "parcel changes"
    ),
    "src/shipping_boxnow/admin.py::BoxNowShipmentAdmin.download_labels_zip": (
        "downloads the labels BoxNow holds; no row and no parcel changes"
    ),
    "src/shipping_boxnow/admin.py::BoxNowShipmentAdmin."
    "download_voucher_action": (
        "downloads the label BoxNow holds; no row and no parcel changes"
    ),
}

SKIP_DIRS = {"migrations", "__pycache__"}


def _decorator_names(tree: ast.Module) -> set[str]:
    """The dotted names that are an admin action decorator in a module,
    read from its imports: ``action`` is DRF's in a views module."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        for alias in node.names:
            bound = alias.asname or alias.name
            if _DECORATOR_MODULES.get(node.module) == alias.name:
                names.add(bound)
            if (node.module, alias.name) in _DECORATOR_PACKAGES:
                names.add(f"{bound}.action")
    return names


def _action_decorator(func: ast.FunctionDef, names: set[str]):
    """``(found, call)``: whether the function is decorated as an admin
    action, and the decorator call when it has arguments."""
    for decorator in func.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call else decorator
        if ast.unparse(target) in names:
            return True, call
    return False, None


def _declared_permissions(call: ast.Call | None) -> list[str] | None:
    """The literal ``permissions=[...]`` of a decorator call, or None when
    it is missing, empty or not a literal list of names."""
    if call is None:
        return None
    for keyword in call.keywords:
        if keyword.arg != "permissions":
            continue
        value = keyword.value
        if not isinstance(value, (ast.List, ast.Tuple)) or not value.elts:
            return None
        if not all(
            isinstance(item, ast.Constant)
            and isinstance(item.value, str)
            and item.value
            for item in value.elts
        ):
            return None
        return [item.value for item in value.elts]
    return None


def _slot_names(class_node: ast.ClassDef) -> set[str]:
    """Every name a class lists in an action slot, dropdown items too."""
    names = set()
    for statement in class_node.body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value:
            targets, value = [statement.target], statement.value
        else:
            continue
        if not any(
            isinstance(target, ast.Name) and target.id in ACTION_SLOTS
            for target in targets
        ):
            continue
        # A name is an element of a list: the slot itself, or the
        # ``items`` of a dropdown. A dropdown's title and icon are not.
        for node in ast.walk(value):
            if isinstance(node, (ast.List, ast.Tuple)):
                names.update(
                    item.value
                    for item in node.elts
                    if isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                )
    return names


def _actions(source: str) -> dict[str, list[str] | None]:
    """``Class.method`` -> its declared permissions (None: none declared)
    for every admin action a module defines."""
    tree = ast.parse(source)
    names = _decorator_names(tree)
    found: dict[str, list[str] | None] = {}

    def visit(body, scope: str, listed: set[str]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, node.name, _slot_names(node))
            elif isinstance(node, ast.FunctionDef):
                decorated, call = _action_decorator(node, names)
                if decorated or node.name in listed:
                    found[f"{scope}.{node.name}"] = _declared_permissions(call)

    visit(tree.body, "<module>", set())
    return found


def _source_actions() -> dict[str, list[str] | None]:
    root = Path(settings.BASE_DIR)
    found = {}
    for path in sorted(Path(settings.SRC_DIR).rglob("*.py")):
        if SKIP_DIRS.intersection(path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        for name, permissions in _actions(
            path.read_text(encoding="utf-8")
        ).items():
            found[f"{rel}::{name}"] = permissions
    return found


def test_every_admin_action_declares_its_permission_in_its_decorator():
    undeclared = sorted(
        label
        for label, permissions in _source_actions().items()
        if permissions is None
    )

    assert undeclared == [], (
        "Give each of these admin actions a literal permissions=[...] in "
        "its decorator (change for an edit, add for a copy, delete for a "
        "deletion). Without one Unfold serves its URL to every signed-in "
        "member of staff."
    )


def test_only_the_named_read_only_actions_ask_for_view_alone():
    view_only = {
        label
        for label, permissions in _source_actions().items()
        if permissions is not None and set(permissions) == {"view"}
    }

    assert sorted(view_only - set(READ_ONLY)) == [], (
        "These actions ask for view alone. One that changes anything "
        "asks for change, add or delete; one that only reads is named in "
        "READ_ONLY with the reason."
    )
    assert sorted(set(READ_ONLY) - view_only) == [], (
        "Remove from READ_ONLY: gone, or no longer view alone."
    )


def test_the_source_guard_sees_the_actions():
    """A guard that matched nothing would pass with the bug wide open."""
    assert len(_source_actions()) > 90


def test_an_action_without_permissions_is_found():
    source = textwrap.dedent(
        """
        from django.contrib import admin
        from unfold.decorators import action as unfold_action

        class ShipmentAdmin:
            actions = ["plain"]
            actions_detail = [{"title": "More", "items": ["in_dropdown"]}]

            @unfold_action(description="Cancel")
            def cancel(self, request, object_id): ...

            @admin.action(description="Retire")
            def retire(self, request, queryset): ...

            @unfold_action
            def bare(self, request): ...

            @unfold_action(permissions=PERMISSIONS)
            def computed(self, request): ...

            @unfold_action(permissions=[])
            def empty(self, request): ...

            def plain(self, request, queryset): ...

            def in_dropdown(self, request, object_id): ...

            @unfold_action(permissions=["change"])
            def declared(self, request): ...

            def not_an_action(self, request): ...
        """
    )

    assert _actions(source) == {
        "ShipmentAdmin.cancel": None,
        "ShipmentAdmin.retire": None,
        "ShipmentAdmin.bare": None,
        "ShipmentAdmin.computed": None,
        "ShipmentAdmin.empty": None,
        "ShipmentAdmin.plain": None,
        "ShipmentAdmin.in_dropdown": None,
        "ShipmentAdmin.declared": ["change"],
    }


def test_a_rest_framework_action_is_not_an_admin_action():
    source = textwrap.dedent(
        """
        from rest_framework.decorators import action

        class OrderViewSet:
            @action(detail=True, methods=["POST"])
            def cancel(self, request, pk=None): ...
        """
    )

    assert _actions(source) == {}


def _slot_entries(model_admin) -> set[str]:
    names = set()
    for slot in ACTION_SLOTS:
        for entry in getattr(model_admin, slot, None) or []:
            if isinstance(entry, dict):
                names.update(entry.get("items", ()))
            elif isinstance(entry, str):
                names.add(entry)
    return names


def _registered_actions():
    for model_admin in django_admin.site._registry.values():
        if not isinstance(model_admin, BaseModelAdmin):
            continue
        for name in sorted(_slot_entries(model_admin)):
            method = getattr(type(model_admin), name, None)
            if method is not None:
                yield f"{type(model_admin).__name__}.{name}", method


def test_every_registered_admin_action_carries_a_permission():
    """What the source guard cannot see: an action a class inherits from a
    mixin or a package and only lists."""
    undeclared = [
        label
        for label, method in _registered_actions()
        if not getattr(method, "allowed_permissions", None)
    ]

    assert not undeclared, (
        "These admin actions declare no permission, so Django and unfold "
        "both offer them to any staff member:\n  " + "\n  ".join(undeclared)
    )


def test_the_registry_guard_sees_the_actions():
    assert sum(1 for _ in _registered_actions()) > 90


@pytest.mark.parametrize(
    ("label", "permissions"),
    [
        ("BoxNowShipmentAdmin.cancel_parcels", ["change"]),
        ("AcsShipmentAdmin.issue_voucher_now", ["change"]),
        ("AcsCodPayoutAdmin.run_reconciliation", ["change"]),
        ("BlogPostAdmin.publish_posts", ["change"]),
        ("BlogCommentAdmin.mark_as_spam", ["delete"]),
        ("TenantAdmin.destroy_tenants", ["delete"]),
    ],
)
def test_the_destructive_ones_specifically(label, permissions):
    """Named so a regression on these reads as itself, not as a count."""
    found = dict(_registered_actions())

    assert label in found, f"{label} is no longer registered"
    assert list(found[label].allowed_permissions) == permissions


def test_every_first_party_admin_extends_the_base():
    """The shared defaults and the two guards above cover admins built
    on ``BaseModelAdmin``.

    A first-party admin on bare ``unfold.admin.ModelAdmin`` skipped
    them; seven did (tenant, contact, core). Third-party admins
    re-wrapped in ``core/admin.py`` are out of scope - their actions are
    the package's, not ours.
    """
    first_party = {
        path.name
        for path in Path(settings.SRC_DIR).iterdir()
        if (path / "__init__.py").exists()
    }
    bare = sorted(
        type(model_admin).__qualname__
        for model_admin in django_admin.site._registry.values()
        if type(model_admin).__module__.split(".")[0] in first_party
        and not isinstance(model_admin, BaseModelAdmin)
        and not type(model_admin).__name__.endswith("AdminOverride")
    )
    assert not bare, f"first-party admins not on BaseModelAdmin: {bare}"
