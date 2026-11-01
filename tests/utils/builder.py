"""A stand-in for the storefront's block catalogue, at the HTTP boundary.

``builder.storefront`` is the only module that talks to the storefront,
and it does so with ``requests``; patching ``requests.get`` there leaves
every line of Django's own code real. The blocks below are small but cover
each rule the validator has: a section with children and a limit, a block
with children of its own, a group-only block, a theme block, a feature
gate, a per-document limit, rich text, references and translatable
settings.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import requests

HASH = "catalogue-hash-1"


def _schema(**properties: Any) -> dict[str, Any]:
    required = [
        name for name, spec in properties.items() if spec.pop("required", False)
    ]
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


TEXT = {"type": "string", "maxLength": 100}


def default_blocks() -> list[dict[str, Any]]:
    return [
        {
            "type": "hero",
            "version": 2,
            "accepts": [1],
            "schema": _schema(
                title={**TEXT, "required": True},
                body={"type": "string", "contentMediaType": "text/html"},
                productId={"type": "integer"},
                categoryId={"type": "integer"},
                pageSlug={"type": "string"},
                mediaId={"type": "integer"},
                blogCategoryId={"type": "integer"},
                bullets={
                    "type": "array",
                    "items": {"type": "string", "maxLength": 20},
                    "maxItems": 3,
                },
            ),
            "translatable": ["/title", "/body", "/bullets"],
            "refs": {
                "/productId": "product",
                "/categoryId": "category",
                "/pageSlug": "page",
                "/mediaId": "media",
                "/blogCategoryId": "blogCategory",
            },
            "limits": {"maxBlocks": 3},
            "children": {"allowed": ["slide"], "maxDepth": 1},
            "kinds": ["section"],
            "templates": ["home"],
        },
        {
            "type": "slide",
            "version": 1,
            "accepts": [],
            "schema": _schema(caption=TEXT),
            "translatable": ["/caption"],
            "refs": {},
            "limits": {},
            "children": {"allowed": []},
            "kinds": ["block"],
        },
        {
            "type": "columns",
            "version": 1,
            "accepts": [],
            "schema": _schema(),
            "translatable": [],
            "refs": {},
            "limits": {"maxPerTemplate": 2},
            "children": {"allowed": ["column"]},
            "kinds": ["section"],
        },
        {
            "type": "column",
            "version": 1,
            "accepts": [],
            "schema": _schema(),
            "translatable": [],
            "refs": {},
            "limits": {},
            "children": {"allowed": ["item"]},
            "kinds": ["block"],
        },
        {
            "type": "item",
            "version": 1,
            "accepts": [],
            "schema": _schema(label=TEXT),
            "translatable": ["/label"],
            "refs": {},
            "limits": {},
            "children": {"allowed": ["item"]},
            "kinds": ["block"],
        },
        {
            "type": "announcement",
            "version": 1,
            "accepts": [],
            "schema": _schema(text={**TEXT, "required": True}),
            "translatable": ["/text"],
            "refs": {},
            "limits": {},
            "children": {"allowed": []},
            "kinds": ["section"],
            "groups": ["header"],
        },
        {
            "type": "theme",
            "version": 1,
            "accepts": [],
            "schema": _schema(radius={"enum": ["none", "md", "lg"]}),
            "translatable": [],
            "refs": {},
            "limits": {},
            "children": {"allowed": []},
            "kinds": ["section"],
            "groups": ["theme"],
        },
        {
            "type": "newsletter",
            "version": 1,
            "accepts": [],
            "schema": _schema(heading=TEXT),
            "translatable": ["/heading"],
            "refs": {},
            "limits": {},
            "children": {"allowed": []},
            "kinds": ["section"],
            "feature": "newsletter",
        },
        {
            "type": "banner",
            "version": 1,
            "accepts": [],
            "schema": _schema(text=TEXT),
            "translatable": ["/text"],
            "refs": {},
            "limits": {},
            "children": {"allowed": []},
            "kinds": ["section"],
        },
    ]


def default_document_kinds() -> dict[str, Any]:
    """A page and a template carry SEO fields at the document's root."""
    seo = {
        "schema": _schema(
            seoTitle={"type": "string", "maxLength": 70},
            seoDescription={"type": "string", "maxLength": 300},
            seoKeywords={"type": "string", "maxLength": 255},
        ),
        "translatable": ["/seoTitle", "/seoDescription", "/seoKeywords"],
    }
    return {"page": seo, "template": seo}


class FakeCatalogue:
    """``requests.get`` for ``/api/builder/catalogue``."""

    def __init__(self, blocks: list[dict[str, Any]] | None = None) -> None:
        self.blocks = default_blocks() if blocks is None else blocks
        self.document_kinds = default_document_kinds()
        self.hash = HASH
        self.calls: list[dict[str, Any]] = []
        self.failure: Exception | None = None
        self.status = 200

    def get(self, url: str, **kwargs: Any) -> mock.Mock:
        self.calls.append({"url": url, **kwargs})
        if self.failure is not None:
            raise self.failure
        response = mock.Mock(spec=requests.Response)
        etag = f'"{self.hash}"'
        if kwargs.get("headers", {}).get("If-None-Match") == etag:
            response.status_code = 304
            response.headers = {"ETag": etag}
            return response
        response.status_code = self.status
        response.headers = {"ETag": etag}
        response.json.return_value = {
            "hash": self.hash,
            "blocks": self.blocks,
            "documentKinds": self.document_kinds,
        }
        return response


def section(
    type_: str, settings: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    return {"type": type_, "settings": settings or {}, **extra}


def document(*sections: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    """``document(("a", section(...)), ...)`` in the given order."""
    return {
        "sections": dict(sections),
        "order": [section_id for section_id, _ in sections],
    }


def hero(title: str = "Welcome", **settings: Any) -> dict[str, Any]:
    return section("hero@2", {"title": title, **settings})
