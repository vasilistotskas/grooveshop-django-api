"""HTML sanitization utility using nh3.

Provides allowlist-based sanitization for user-generated HTML content
(product descriptions, blog posts). Strips dangerous elements (script,
event handlers) while preserving safe formatting tags.
"""

from urllib.parse import urlsplit

import nh3

# Origins an embedded ``<iframe>`` may point at: the players TinyMCE's
# ``media`` plugin emits for a pasted YouTube or Vimeo link. Must match
# ``EMBED_IFRAME_ORIGINS`` in the storefront's ``shared/utils/embeds.ts``,
# which re-sanitises the same markup and lists these origins in the CSP
# ``frame-src``. An origin missing here is stripped on save, so the video
# is gone from the row before the storefront ever sees it.
EMBED_IFRAME_ORIGINS = frozenset(
    {
        # ``youtube-nocookie`` is what TinyMCE emits in privacy-enhanced
        # mode, so both are needed or embeds depend on an editor toggle.
        "https://www.youtube.com",
        "https://www.youtube-nocookie.com",
        "https://player.vimeo.com",
    }
)

# Tags allowed in rich-text content fields
ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    # Embedded video only: ``src`` is pinned to EMBED_IFRAME_ORIGINS by
    # ``_filter_attribute``, and ``srcdoc`` is never allowed.
    "iframe",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    # Sectioning content. The legal documents seeded into every tenant's
    # ContentPage rows wrap each clause in ``<section id="...">`` so the
    # table of contents has something to anchor to; stripping it would
    # have silently flattened those anchors on save. Purely semantic —
    # nh3 keeps no behaviour with it.
    "section",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

# Attributes allowed per tag
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "iframe": {
        "src",
        "width",
        "height",
        "title",
        "allow",
        "allowfullscreen",
        "frameborder",
        "loading",
        "referrerpolicy",
    },
    "img": {"src", "alt", "width", "height", "loading"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": {"class", "id", "style"},
}


def is_allowed_embed_url(src: str) -> bool:
    """True when ``src`` is an absolute https URL on an embed origin.

    Compares the parsed origin, never a substring: ``youtube.com`` in
    ``https://youtube.com.evil.example/`` or in a query string must not
    pass. A ``netloc`` carrying userinfo (``https://www.youtube.com@evil``)
    cannot equal a listed origin either.
    """
    parts = urlsplit(src.strip())
    return f"{parts.scheme}://{parts.netloc.lower()}" in EMBED_IFRAME_ORIGINS


def _filter_attribute(tag: str, attr: str, value: str) -> str | None:
    # nh3 cannot drop an element from an attribute filter, so an iframe
    # pointing elsewhere keeps only its inert attributes and renders
    # ``about:blank``; the storefront sanitiser then removes it.
    if tag == "iframe" and attr == "src" and not is_allowed_embed_url(value):
        return None
    return value


def sanitize_html(html: str) -> str:
    """Sanitize HTML content using an allowlist of safe tags and attributes.

    Removes script tags, event handler attributes, and any other
    potentially dangerous HTML while preserving safe formatting.
    """
    if not html:
        return html

    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_filter_attribute,
        link_rel="noopener noreferrer",
    )
