"""The HTML policy for admin-authored rich text.

One allowlist, applied by ``core.fields.rich_text.RichTextField`` to
every rich-text column: product and category descriptions, blog bodies,
content pages and payment instructions. The storefront re-sanitises the
same markup on render, so this module decides what can be stored and
the storefront can only ever show less.

The allowlist covers everything the TinyMCE plugins enabled in
``settings.TINYMCE_DEFAULT_CONFIG`` emit. That was measured against the
bundled editor, not assumed: strikethrough is ``<s>``, a table carries
``border`` plus a ``<colgroup>`` of ``<col>`` widths, and a YouTube or
Vimeo link becomes an ``<iframe>``. Every one of those used to be
stripped on save while the editor kept showing it, so a post looked
right in the admin and lost its video on the storefront. When an editor
plugin is added, re-check what it emits against this list.

What still falls outside the policy — pasted source, a video file, an
embed from another host — is refused by ``removed_markup`` at validation
time instead of disappearing.
"""

from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urlsplit

import nh3

# Origins an embedded ``<iframe>`` may point at: the players TinyMCE's
# ``media`` plugin emits for a pasted YouTube or Vimeo link. Must match
# ``EMBED_IFRAME_ORIGINS`` in the storefront's ``shared/utils/embeds.ts``,
# which re-sanitises the same markup and lists these origins in the CSP
# ``frame-src``.
EMBED_IFRAME_ORIGINS = frozenset(
    {
        # ``youtube-nocookie`` is what TinyMCE emits in privacy-enhanced
        # mode, so both are needed or embeds depend on an editor toggle.
        "https://www.youtube.com",
        "https://www.youtube-nocookie.com",
        "https://player.vimeo.com",
    }
)

ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "caption",
    "code",
    "col",
    "colgroup",
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
    # TinyMCE's strikethrough.
    "s",
    # Sectioning content. The legal documents seeded into every tenant's
    # ContentPage rows wrap each clause in ``<section id="...">`` so the
    # table of contents has something to anchor to. Purely semantic —
    # nh3 keeps no behaviour with it.
    "section",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "col": {"span"},
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
    "table": {"border"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "*": {"class", "id", "style"},
}

# nh3 always sets ``rel`` on links itself, so ``rel`` is not in the
# allowlist and the one an editor typed is replaced rather than kept.
_LINK_REL = "noopener noreferrer"


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
    # ``about:blank``. Validation refuses it before it gets that far.
    if tag == "iframe" and attr == "src" and not is_allowed_embed_url(value):
        return None
    return value


def sanitize_html(html: str) -> str:
    """Return ``html`` reduced to the rich-text policy."""
    if not html:
        return html

    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_filter_attribute,
        link_rel=_LINK_REL,
    )


class _MarkupCounter(HTMLParser):
    """Counts every element and every ``element[attribute]`` in a fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.counts: Counter[str] = Counter()

    def handle_starttag(self, tag, attrs):
        self.counts[f"<{tag}>"] += 1
        for name, _value in attrs:
            self.counts[f"<{tag} {name}>"] += 1

    handle_startendtag = handle_starttag


def _markup(html: str) -> Counter[str]:
    counter = _MarkupCounter()
    counter.feed(html)
    counter.close()
    return counter.counts


def removed_markup(html: str) -> list[str]:
    """What ``sanitize_html`` would drop from ``html``, sorted.

    Each entry is an element (``<video>``) or, for an element that is
    kept, one of its attributes (``<iframe src>``). Derived from nh3's
    own output rather than a second reading of the policy, so the two
    cannot disagree. Only removals count: the parser also *adds* markup
    to repair a fragment (an implied ``<tbody>``, a closing tag), and
    that loses nothing. Comments are not reported; they never render.
    """
    if not html:
        return []
    removed = _markup(html) - _markup(sanitize_html(html))
    dropped_elements = {key[1:-1] for key in removed if " " not in key}
    return sorted(
        key
        for key in removed
        if " " not in key or key[1:].split(" ", 1)[0] not in dropped_elements
    )
