"""A caller's address is read only through ``core.client_ip``.

``REMOTE_ADDR`` is the proxy's address, ``X-Forwarded-For`` and
``X-Real-IP`` are chosen by whoever sends the request, and which of them
may be believed is the one decision ``core/client_ip.py`` makes
(``edge_client_ip``, ``client_ip_or_peer``). A second reader re-decides it,
usually wrongly: keying a throttle on the proxy shares one budget between
all callers, trusting a header lets a caller mint its own.

This walks the AST of every application module for a read of one of the
four keys, as a ``META`` / ``headers`` subscript or a ``.get(...)``
argument, in any spelling of the header name, written as a string or as a
module-level ``NAME = "..."`` constant.

It is not complete. It does NOT catch a header name computed at runtime
(``getattr``, an f-string, a loop over a tuple of names), a read through a
third-party helper such as DRF's ``get_ident``, or a header named in
another module's constant.
"""

from __future__ import annotations

import ast
import textwrap
from collections import Counter
from pathlib import Path

from django.conf import settings

CLIENT_IP_MODULE = "src/core/client_ip.py"

# The META and header spellings of the four client-address inputs,
# compared after normalising ``-`` and ``HTTP_`` and the case.
_FORBIDDEN = frozenset(
    {"REMOTE_ADDR", "X_FORWARDED_FOR", "X_REAL_IP", "CF_CONNECTING_IP"}
)

# Reads that stay: file -> (reason, {(enclosing function, header): count}).
# Keyed by the exact reads, so a new read in one of these files fails as
# a new violation. An entry only shrinks, by routing the read through the
# helper.
ALLOWED: dict[str, tuple[str, Counter[tuple[str, str]]]] = {
    # Every read is an argument to ``logger.info`` / ``logger.warning``
    # (they print REMOTE_ADDR, X-Forwarded-For and X-Real-IP); none feeds
    # a decision. The decisions in this module use ``client_ip_or_peer``.
    "src/order/views/viva_webhook.py": (
        "log lines only, decides nothing",
        Counter(
            {
                ("_handle_verification", "REMOTE_ADDR"): 2,
                ("_handle_verification", "X_FORWARDED_FOR"): 1,
                ("_handle_webhook_event", "REMOTE_ADDR"): 1,
                ("_handle_webhook_event", "X_FORWARDED_FOR"): 1,
                ("_handle_webhook_event", "X_REAL_IP"): 1,
            }
        ),
    ),
}
SKIP_DIRS = {
    "tests",
    "tests_mt",
    "migrations",
    ".venv",
    "node_modules",
    ".claude",
}


def _normalise(name: str) -> str:
    name = name.upper().replace("-", "_")
    return name.removeprefix("HTTP_")


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` assignments."""
    return {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


class _Reads(ast.NodeVisitor):
    """Every ``x[KEY]`` and ``x.get(KEY, ...)`` with a client-address key,
    as ``(enclosing function, normalised key)``."""

    def __init__(self, constants: dict[str, str]):
        self.constants = constants
        self.scope: list[str] = []
        self.found: Counter[tuple[str, str]] = Counter()

    def _key(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            value = self.constants.get(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
        else:
            return None
        if value is None:
            return None
        key = _normalise(value)
        return key if key in _FORBIDDEN else None

    def _record(self, node: ast.expr) -> None:
        key = self._key(node)
        if key:
            self.found[(".".join(self.scope) or "<module>", key)] += 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._record(node.slice)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"get", "pop", "setdefault"}
            and node.args
        ):
            self._record(node.args[0])
        self.generic_visit(node)


def _reads(source: str) -> Counter[tuple[str, str]]:
    tree = ast.parse(source)
    visitor = _Reads(_string_constants(tree))
    visitor.visit(tree)
    return visitor.found


def _app_modules():
    root = Path(settings.BASE_DIR)
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        yield path.relative_to(root).as_posix(), path


def _violations() -> dict[str, Counter[tuple[str, str]]]:
    found = {}
    for rel, path in _app_modules():
        if rel == CLIENT_IP_MODULE:
            continue
        reads = _reads(path.read_text(encoding="utf-8"))
        if reads:
            found[rel] = reads
    return found


def test_only_the_client_ip_module_reads_the_address_inputs():
    unexpected = {
        rel: dict(reads)
        for rel, reads in _violations().items()
        if reads != ALLOWED.get(rel, ("", Counter()))[1]
    }
    assert unexpected == {}, (
        "Read the caller's address through core.client_ip "
        "(edge_client_ip, client_ip_or_peer), not REMOTE_ADDR, "
        "X-Forwarded-For, X-Real-IP or CF-Connecting-IP directly. "
        "Keys are (enclosing function, header)."
    )


def test_every_allowed_module_still_reads_one():
    stale = sorted(set(ALLOWED) - set(_violations()))
    assert stale == [], f"Remove from ALLOWED, no longer reading: {stale}"


def test_a_constant_header_name_is_still_a_read():
    source = textwrap.dedent(
        """
        HEADER = "HTTP_CF_CONNECTING_IP"

        def f(request):
            return request.META.get(HEADER)
        """
    )
    assert _reads(source) == Counter({("f", "CF_CONNECTING_IP"): 1})
