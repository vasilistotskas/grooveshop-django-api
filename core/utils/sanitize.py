"""HTML sanitization utility using nh3.

Provides allowlist-based sanitization for user-generated HTML content
(product descriptions, blog posts). Strips dangerous elements (script,
event handlers) while preserving safe formatting tags.
"""

import nh3

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
    "img",
    "li",
    "ol",
    "p",
    "pre",
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
    "img": {"src", "alt", "width", "height", "loading"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "*": {"class", "id", "style"},
}


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
        link_rel="noopener noreferrer",
    )
