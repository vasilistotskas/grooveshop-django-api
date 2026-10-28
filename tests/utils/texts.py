"""A stand-in for the storefront's text endpoints, at the HTTP boundary.

``texts.storefront`` is the only module that talks to the storefront, and
it does so with ``requests``; patching ``requests.get``/``requests.post``
there leaves every line of Django's own code real.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

import requests

FOOTER = "components/Chrome/Footer"
HEADER = "components/Chrome/Navbar"
COOKIES = "components/Cookie/Control"

NO_PLACEHOLDERS = {"params": [], "pluralCases": 1}


def entry(
    namespace: str,
    key: str,
    *,
    el: str | None = None,
    en: str | None = None,
    locked: bool = False,
    params: list[str] | None = None,
    plural_cases: int = 1,
) -> dict[str, Any]:
    defaults = {
        locale: text for locale, text in (("el", el), ("en", en)) if text
    }
    shape = {"params": params or [], "pluralCases": plural_cases}
    return {
        "namespace": namespace,
        "key": key,
        "defaults": defaults,
        "placeholders": {locale: shape for locale in defaults},
        "locked": locked,
    }


def default_entries() -> list[dict[str, Any]]:
    return [
        entry(FOOTER, "tagline", el="Το κατάστημά μας", en="Our shop"),
        entry(
            FOOTER,
            "greeting",
            el="Γεια σου {name}",
            en="Hello {name}",
            params=["named:name"],
        ),
        entry(HEADER, "menu", el="Μενού", en="Menu"),
        entry(FOOTER, "only_greek", el="Μόνο ελληνικά"),
        entry(COOKIES, "banner", el="Cookies", en="Cookies", locked=True),
    ]


def catalogue_payload(
    entries: list[dict[str, Any]] | None = None, *, build: str = "h1"
) -> dict[str, Any]:
    return {
        "hash": build,
        "locales": ["el", "en"],
        "entries": entries if entries is not None else default_entries(),
    }


def response(
    status: int, body: Any = None, headers: dict[str, str] | None = None
) -> mock.Mock:
    reply = mock.Mock(spec=requests.Response)
    reply.status_code = status
    reply.headers = headers or {}
    reply.json.return_value = body
    return reply


@dataclass
class FakeStorefront:
    """What the patched ``requests`` answers, and what it was asked."""

    catalogue: dict[str, Any] = field(default_factory=catalogue_payload)
    etag: str = '"h1"'
    #: ``(namespace, key, locale, value) -> None | (code, detail)``
    verdict: Callable[..., tuple[str, str] | None] = lambda *a: None
    down: bool = False
    gets: list[dict[str, Any]] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)

    def get(self, url: str, **kwargs: Any) -> mock.Mock:
        self.gets.append({"url": url, **kwargs})
        if self.down:
            raise requests.ConnectionError("storefront down")
        if kwargs.get("headers", {}).get("If-None-Match") == self.etag:
            return response(304)
        return response(200, self.catalogue, {"ETag": self.etag})

    def post(self, url: str, **kwargs: Any) -> mock.Mock:
        self.posts.append({"url": url, **kwargs})
        if self.down:
            raise requests.ConnectionError("storefront down")
        body = kwargs["json"]
        verdict = self.verdict(
            body["namespace"], body["key"], body["locale"], body["value"]
        )
        if verdict is None:
            return response(200, {"ok": True})
        code, detail = verdict
        return response(422, {"ok": False, "code": code, "detail": detail})
