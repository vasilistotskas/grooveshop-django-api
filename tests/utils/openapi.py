"""Validate a real response body against the published OpenAPI document.

The storefront generates its Zod schemas from ``schema.yml`` and answers
422 to any body they reject, so the useful check is the storefront's:
does this JSON satisfy what its operation documents? The document is
OpenAPI 3.0, whose ``nullable`` JSON Schema does not know; the components
are rewritten once per document into JSON Schema ``definitions`` (each
``nullable`` becomes a ``"null"`` alternative, each ``$ref`` points into
``definitions``, so a schema that refers to itself, a field error holding
field errors, can be judged). The rest is JSON Schema draft 4's
vocabulary (a boolean ``exclusiveMaximum``), so draft 4 judges.

A ``format`` is checked too (``FORMATS``): the storefront's parsers reject
a malformed ``uuid``, ``date``, ``date-time``, ``email`` or ``uri``, and
drf-spectacular declares a blank-or-formatted string (``EmailField``,
``URLField`` with ``allow_blank``) as a ``oneOf`` that only the format
tells apart: without it ``""`` matches both branches.

``tests/response_contract.py`` applies this to every response the suite
receives; the functions here are for a test that judges a body itself.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import date, datetime
from functools import cache
from typing import Any
from urllib.parse import urlsplit

import jsonschema
from jsonschema.protocols import Validator

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# RFC 3339 as zod 4's ``iso.datetime({offset: true})`` reads it: hours
# 00-23, seconds required, a fraction optional, ``Z`` or an offset.
_DATE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d+)?"
    r"(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)
_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*")
# The storefront's own patterns (zod 4, ``v4/core/regexes.js``: ``uuid()``
# and ``email``): a body that passes here passes its parser.
_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}"
    r"-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000"
    r"|ffffffff-ffff-ffff-ffff-ffffffffffff"
)
_EMAIL = re.compile(
    r"(?:[A-Za-z0-9_'+\-]+\.)*[A-Za-z0-9_'+\-]*[A-Za-z0-9_+-]"
    r"@(?:[A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}"
)
# WHATWG's special schemes other than ``file``: a URL of one has a host,
# which may not hold a forbidden domain code point.
_SCHEMES_WITH_A_HOST = frozenset({"http", "https", "ws", "wss", "ftp"})
_FORBIDDEN_IN_HOST = re.compile(r"[\x00-\x20#%/:<>?@\[\\\]^|\x7f]")

#: The formats the document uses that a client checks; ``double`` and
#: ``binary`` constrain nothing a JSON body carries.
FORMATS = jsonschema.FormatChecker(formats=())


@FORMATS.checks("date", raises=ValueError)
def _is_date(value: object) -> bool:
    if not isinstance(value, str):
        return True
    return bool(_DATE.fullmatch(value)) and bool(date.fromisoformat(value))


@FORMATS.checks("date-time", raises=ValueError)
def _is_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True
    return bool(_DATE_TIME.fullmatch(value)) and bool(
        datetime.fromisoformat(value)
    )


@FORMATS.checks("uuid")
def _is_uuid(value: object) -> bool:
    return not isinstance(value, str) or bool(_UUID.fullmatch(value))


@FORMATS.checks("email")
def _is_email(value: object) -> bool:
    return not isinstance(value, str) or bool(_EMAIL.fullmatch(value))


@FORMATS.checks("uri", raises=ValueError)
def _is_uri(value: object) -> bool:
    """What ``URL.canParse`` accepts (the storefront's ``z.url()``), or
    stricter: a scheme; for a special scheme a host without a forbidden
    code point, an address when it ends in a number, and a port in range.
    A relative path is not a URL. ``urlsplit`` raises ``ValueError`` on a
    malformed bracketed host or port, which fails the value."""
    if not isinstance(value, str):
        return True
    parts = urlsplit(value.strip())
    if not _SCHEME.fullmatch(parts.scheme):
        return False
    if parts.scheme.lower() not in _SCHEMES_WITH_A_HOST:
        return True
    parts.port  # noqa: B018 - raises ValueError for a port out of range
    host = parts.hostname or ""
    if not host:
        return False
    if "[" in parts.netloc:
        ipaddress.IPv6Address(host)
        return True
    if _FORBIDDEN_IN_HOST.search(host):
        return False
    if host.rstrip(".").rsplit(".", 1)[-1].isdigit():
        ipaddress.IPv4Address(host.rstrip("."))
    return True


@cache
def openapi_document() -> dict:
    """The OpenAPI document ``manage.py spectacular`` writes, built once per
    process.

    The command is this same call plus rendering
    (``drf_spectacular/management/commands/spectacular.py``), and CI fails a
    change whose committed ``schema.yml`` differs from it. Shared by every
    test: read it, never mutate it.
    """
    from drf_spectacular.settings import spectacular_settings

    generator = spectacular_settings.DEFAULT_GENERATOR_CLASS()
    return generator.get_schema(request=None, public=True)


def _as_json_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_as_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    converted = {
        key: (
            "#/definitions/" + item.rsplit("/", 1)[-1]
            if key == "$ref" and isinstance(item, str)
            else _as_json_schema(item)
        )
        for key, item in value.items()
        if key != "nullable"
    }
    if value.get("nullable"):
        return {"anyOf": [converted, {"type": "null"}]}
    return converted


class Judge:
    """One document's components as JSON Schema, converted once, and a
    validator per schema node asked about."""

    def __init__(self, document: dict) -> None:
        self.document = document
        self._definitions = {
            name: _as_json_schema(component)
            for name, component in document["components"]["schemas"].items()
        }
        self._validators: dict[int, tuple[dict, Validator]] = {}

    def validator(self, node: dict) -> Validator:
        """The validator for ``node``. The cache keeps ``node`` beside its
        validator, so the ``id`` it is filed under cannot be reused by
        another dict while the entry lives."""
        found = self._validators.get(id(node))
        if found is None:
            schema = _as_json_schema(node)
            schema["definitions"] = self._definitions
            found = self._validators[id(node)] = (
                node,
                jsonschema.Draft4Validator(schema, format_checker=FORMATS),
            )
        return found[1]


_JUDGES: dict[int, Judge] = {}


def judge(document: dict) -> Judge:
    """The :class:`Judge` of ``document``, made on first use. It holds the
    document, so the ``id`` it is filed under cannot be reused."""
    found = _JUDGES.get(id(document))
    if found is None:
        found = _JUDGES[id(document)] = Judge(document)
    return found


def response_schema(
    openapi: dict, path: str, method: str = "get", status: str = "200"
) -> dict:
    content = openapi["paths"][path][method]["responses"][status]["content"]
    return content["application/json"]["schema"]


def validate_against(openapi: dict, node: Any, body: Any) -> None:
    """Raise if ``body`` does not satisfy ``node``, a schema of ``openapi``."""
    judge(openapi).validator(node).validate(body)


def assert_matches_response(
    openapi: dict,
    path: str,
    body: Any,
    method: str = "get",
    status: str = "200",
) -> None:
    """Raise if ``body`` is not what ``path``'s ``status`` response (200 by
    default) documents."""
    validate_against(
        openapi, response_schema(openapi, path, method, status), body
    )
