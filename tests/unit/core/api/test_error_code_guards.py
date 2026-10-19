"""Static guards for the error contract.

A code is a stable identifier the client branches on; a message is the
translated sentence it shows. Mixing them up is how a review refusal
reached a Greek shopper as a generic failure: the code ``must_have_purchased``
was the gettext msgid, so the wire carried whatever it translated to and
the storefront compared that text. These checks read the source, so they
need no database.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]

GETTEXT = {"_", "gettext", "gettext_lazy", "ngettext", "pgettext"}
API_EXCEPTIONS = {
    "ValidationError",
    "APIException",
    "PermissionDenied",
    "NotFound",
    "NotAuthenticated",
    "AuthenticationFailed",
    "Throttled",
    "ParseError",
    "MethodNotAllowed",
    "NotAcceptable",
}
#: ``must_have_purchased``: lowercase words joined by underscores. A real
#: sentence has a space; a lone word ("Required") is not matched either.
MSGID_LIKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")

#: Modules where every ``ValidationError`` raise carries a ``code=``. The
#: other flows are a follow-up; add a module here when it is done.
CODED_MODULES = (
    "product/views/review.py",
    "product/serializers/review.py",
    "product/serializers/favourite.py",
    "product/serializers/alert.py",
    "cart/serializers/item.py",
    "cart/serializers/cart.py",
    "user/serializers/address.py",
    "user/serializers/account.py",
    "blog/serializers/comment.py",
    "order/serializers/item.py",
    "order/serializers/order.py",
    "order/views/order.py",
)


def _python_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [
        ROOT / name
        for name in tracked
        if not name.startswith(("tests", "tests_mt", ".claude"))
        and "/migrations/" not in name
    ]


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else None


def msgid_like_gettext_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Every ``_("snake_case")`` under an API exception's arguments."""
    found = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call) and _name(node.func) in API_EXCEPTIONS
        ):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and _name(inner.func) in GETTEXT
                and inner.args
                and isinstance(inner.args[0], ast.Constant)
                and isinstance(inner.args[0].value, str)
                and MSGID_LIKE.match(inner.args[0].value)
            ):
                found.append((inner.lineno, inner.args[0].value))
    return found


def uncoded_validation_raises(tree: ast.AST) -> list[int]:
    """Lines of ``raise ValidationError(<message>)`` with no ``code=``.

    A raise of a bare name (an error dict built by a helper that codes its
    own messages) is not judged here.
    """
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and _name(node.exc.func) == "ValidationError"
        ):
            continue
        call = node.exc
        if any(keyword.arg == "code" for keyword in call.keywords):
            continue
        if len(call.args) > 1 or not call.args:
            continue
        if isinstance(call.args[0], ast.Name):
            continue
        lines.append(node.lineno)
    return lines


def _parse(source: str) -> ast.AST:
    return ast.parse(source)


def test_no_error_code_goes_through_gettext():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [
            f"{path.relative_to(ROOT)}:{line} _({code!r})"
            for line, code in msgid_like_gettext_calls(tree)
        ]

    assert not offenders, (
        "A stable code must be passed as code=..., never through gettext; "
        "translate the sentence instead:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("module", CODED_MODULES)
def test_every_validation_error_of_a_coded_module_has_a_code(module):
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))

    assert uncoded_validation_raises(tree) == [], (
        f"{module}: give each ValidationError a code=..."
    )


class TestTheGuardsSeeTheirTarget:
    """The guards must fail on what they exist to catch."""

    def test_a_code_passed_through_gettext_is_found(self):
        tree = _parse(
            'raise ValidationError({"product": _("must_have_purchased")})'
        )

        assert msgid_like_gettext_calls(tree) == [(1, "must_have_purchased")]

    def test_a_sentence_and_a_code_keyword_are_not(self):
        tree = _parse(
            "raise ValidationError(\n"
            '    {"product": _("You must have bought it.")},\n'
            '    code="must_have_purchased",\n'
            ")"
        )

        assert msgid_like_gettext_calls(tree) == []

    def test_an_uncoded_raise_is_found(self):
        tree = _parse('raise ValidationError(_("Nope."))')

        assert uncoded_validation_raises(tree) == [1]

    def test_a_coded_raise_and_a_helper_dict_are_not(self):
        tree = _parse(
            'raise ValidationError(_("Nope."), code="nope")\n'
            "raise ValidationError(errors)\n"
        )

        assert uncoded_validation_raises(tree) == []
