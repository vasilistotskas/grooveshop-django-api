"""A caller's address is read only through ``core.client_ip``.

``REMOTE_ADDR`` is the proxy's address, ``X-Forwarded-For`` and
``X-Real-IP`` are chosen by whoever sends the request, and which of them
may be believed is the one decision ``core/client_ip.py`` makes
(``edge_client_ip``, ``client_ip_or_peer``). A second reader re-decides it,
usually wrongly: keying a throttle on the proxy shares one budget between
all callers, trusting a header lets a caller mint its own.

This walks the AST of every application module for a read of one of the
three keys, as a ``META`` / ``headers`` subscript or a ``.get(...)``
argument, in any spelling of the header name.
"""

from __future__ import annotations

import ast
from pathlib import Path

from django.conf import settings

CLIENT_IP_MODULE = "core/client_ip.py"

# The META and header spellings of the three client-address inputs,
# compared after normalising ``-`` and ``HTTP_`` and the case.
_FORBIDDEN = frozenset({"REMOTE_ADDR", "X_FORWARDED_FOR", "X_REAL_IP"})

# Reads that stay, each with its reason. A module here is not a model to
# copy: an entry only shrinks, by routing the read through the helper.
ALLOWED = {
    # Every read is an argument to ``logger.info`` / ``logger.warning``
    # (they print REMOTE_ADDR, X-Forwarded-For and X-Real-IP); none feeds
    # a decision. The decisions in this module use ``client_ip_or_peer``.
    "order/views/viva_webhook.py": "log lines only, decides nothing",
    # Believes X-Real-IP without the edge proof, which the helper refuses.
    # A potential defect, not a decision: issue #638.
    "meta_capi/signals.py": "believes X-Real-IP; issue #638",
    # Its own copy of the peer rule, storing an unchecked forwarded hop in
    # a GenericIPAddressField: issue #639.
    "search/middleware.py": "own copy of the peer rule; issue #639",
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


def _is_forbidden_literal(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _normalise(node.value) in _FORBIDDEN
    )


def _reads(tree: ast.AST) -> list[int]:
    """Line of every ``x[KEY]`` and ``x.get(KEY, ...)`` with a client key."""
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and _is_forbidden_literal(node.slice)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "pop", "setdefault"}
                and node.args
                and _is_forbidden_literal(node.args[0])
            )
        ):
            lines.append(node.lineno)
    return lines


def _app_modules():
    root = Path(settings.BASE_DIR)
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        yield path.relative_to(root).as_posix(), path


def _violations() -> dict[str, list[int]]:
    found = {}
    for rel, path in _app_modules():
        if rel == CLIENT_IP_MODULE:
            continue
        lines = _reads(ast.parse(path.read_text(encoding="utf-8")))
        if lines:
            found[rel] = lines
    return found


def test_only_the_client_ip_module_reads_the_address_inputs():
    unexpected = {
        rel: lines for rel, lines in _violations().items() if rel not in ALLOWED
    }
    assert unexpected == {}, (
        "Read the caller's address through core.client_ip "
        "(edge_client_ip, client_ip_or_peer), not REMOTE_ADDR, "
        "X-Forwarded-For or X-Real-IP directly."
    )


def test_every_allowed_module_still_reads_one():
    stale = sorted(set(ALLOWED) - set(_violations()))
    assert stale == [], f"Remove from ALLOWED, no longer reading: {stale}"
