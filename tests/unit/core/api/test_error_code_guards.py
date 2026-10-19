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


def _imports_drf_validation_error(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("rest_framework")
        and any(alias.name == "ValidationError" for alias in node.names)
        for node in ast.walk(tree)
    )


def uncoded_validation_raises(tree: ast.AST) -> list[int]:
    """Lines of DRF ``raise ValidationError(<message>)`` with no ``code=``.

    Django's own ``ValidationError`` (model ``clean()``, admin forms) is not
    judged: it is staff-facing, and DRF reads its code only through a
    serializer. A raise of a bare name (an error dict built by a helper
    that codes its own messages) is not judged either.
    """
    drf_name = _imports_drf_validation_error(tree)
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and _name(node.exc.func) == "ValidationError"
        ):
            continue
        call = node.exc
        if isinstance(call.func, ast.Name) and not drf_name:
            continue
        if isinstance(call.func, ast.Attribute) and not (
            isinstance(call.func.value, ast.Name)
            and call.func.value.id in {"serializers", "exceptions"}
        ):
            continue  # forms.ValidationError and the like are Django's
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


def test_every_drf_validation_error_has_a_code():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [
            f"{path.relative_to(ROOT)}:{line}"
            for line in uncoded_validation_raises(tree)
        ]

    assert not offenders, "Give each ValidationError a code=...:\n" + "\n".join(
        offenders
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
        tree = _parse('raise serializers.ValidationError(_("Nope."))')

        assert uncoded_validation_raises(tree) == [1]

    def test_a_django_validation_error_is_not_judged(self):
        tree = _parse(
            "from django.core.exceptions import ValidationError\n"
            'raise ValidationError(_("Nope."))'
        )

        assert uncoded_validation_raises(tree) == []

    def test_a_drf_name_import_is_judged(self):
        tree = _parse(
            "from rest_framework.exceptions import ValidationError\n"
            'raise ValidationError(_("Nope."))'
        )

        assert uncoded_validation_raises(tree) == [2]

    def test_a_coded_raise_and_a_helper_dict_are_not(self):
        tree = _parse(
            'raise ValidationError(_("Nope."), code="nope")\n'
            "raise ValidationError(errors)\n"
        )

        assert uncoded_validation_raises(tree) == []


#: Responses a provider calls back or another protocol defines: not an API
#: error a shopper's client reads.
UNCODED_RESPONSE_MODULES = frozenset(
    {
        "order/views/viva_webhook.py",
        "shipping_boxnow/views/webhook.py",
        "core/middleware/allauth_ratelimit.py",
    }
)


def uncoded_error_responses(tree: ast.AST) -> list[int]:
    """Lines of ``Response({...}, status=4xx/5xx)`` whose dict has no ``code``.

    Only a literal dict is judged: a body built elsewhere (a serializer's
    errors, a variable) is not.
    """
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and _name(node.func) in {"Response", "JsonResponse"}
            and node.args
            and isinstance(node.args[0], ast.Dict)
        ):
            continue
        status = next(
            (k.value for k in node.keywords if k.arg == "status"), None
        )
        if status is None:
            continue
        text = ast.unparse(status)
        if not re.search(r"HTTP_[45]\d\d|^[45]\d\d$", text):
            continue
        keys = {
            k.value for k in node.args[0].keys if isinstance(k, ast.Constant)
        }
        if "code" not in keys:
            lines.append(node.lineno)
    return lines


def test_every_error_response_a_view_builds_has_a_code():
    offenders = []
    for path in _python_files():
        name = str(path.relative_to(ROOT)).replace("\\", "/")
        if name in UNCODED_RESPONSE_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [
            f"{name}:{line}" for line in uncoded_error_responses(tree)
        ]

    assert not offenders, (
        "A 4xx/5xx Response({...}) needs a code key:\n" + "\n".join(offenders)
    )


def test_the_response_guard_sees_an_uncoded_body():
    tree = _parse(
        'Response({"detail": "x"}, status=status.HTTP_400_BAD_REQUEST)\n'
        'Response({"detail": "x", "code": "y"}, status=404)\n'
        'Response({"detail": "x"}, status=status.HTTP_200_OK)\n'
    )

    assert uncoded_error_responses(tree) == [1]
