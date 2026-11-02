"""Parser and renderer for the builder API.

The API camelCases every key (``djangorestframework-camel-case``), which
is wrong for a document: its keys are the storefront's own (a section
id, a setting name) and must reach the storefront exactly as the editor
wrote them. These two leave the content fields alone; the envelope around
them is camelCased as everywhere else.
"""

from __future__ import annotations

from djangorestframework_camel_case.parser import CamelCaseJSONParser
from djangorestframework_camel_case.render import CamelCaseJSONRenderer

#: Fields whose value is a document (or a part of one).
DOCUMENT_FIELDS = (
    "draft",
    "data",
    "i18n",
    "theme",
    "groups",
    "page",
    "versions",
)

#: ``i18n`` is also kept as written: camelCase conversion would turn it
#: into ``i_18n`` on the way in.
_OPTIONS = {"ignore_fields": DOCUMENT_FIELDS, "ignore_keys": ("i18n",)}


class BuilderJSONRenderer(CamelCaseJSONRenderer):
    json_underscoreize = _OPTIONS


class BuilderJSONParser(CamelCaseJSONParser):
    json_underscoreize = _OPTIONS


#: The link labels answer holds ids as KEYS (a content page's slug may carry
#: an underscore), which camel-casing would rewrite.
_LABEL_OPTIONS = {
    "ignore_fields": (*DOCUMENT_FIELDS, "contentPage"),
    "ignore_keys": ("i18n",),
}


class LinkLabelsRenderer(CamelCaseJSONRenderer):
    json_underscoreize = _LABEL_OPTIONS
