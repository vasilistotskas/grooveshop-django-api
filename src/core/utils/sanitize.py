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

Pasted HTML is the other source, and TinyMCE keeps every attribute of
it. The allowlist takes the ones that mean something (``dir``, ``lang``,
``title``, list numbering); the rest is residue no reader sees.

Content that still falls outside the policy — pasted source, a video
file, an embed from another host, a ``javascript:`` link — is refused by
``lost_content`` at validation time instead of disappearing. Invisible
residue (``data-*``, ``aria-*``, event handlers) is normalised away.
"""

import re
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

# The real pixel size of an uploaded image, written on save by
# ``core.utils.rich_text_images`` and read by the storefront. Allowed
# here so a later save keeps what it recomputes anyway.
NATURAL_WIDTH_ATTR = "data-natural-width"
NATURAL_HEIGHT_ATTR = "data-natural-height"

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
    # FAQ items: the accordion plugin's <details class="mce-accordion">
    # with a <summary> question. Saved closed (TinyMCE
    # ``details_serialized_state``), so ``open`` is not allowed.
    "details",
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
    "summary",
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
    # ``rel`` is kept only so ``_SET_ATTRIBUTES`` can overwrite it in place.
    "a": {"href", "rel", "title", "target"},
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
    "img": {
        "src",
        "alt",
        "width",
        "height",
        "loading",
        NATURAL_WIDTH_ATTR,
        NATURAL_HEIGHT_ATTR,
    },
    # The lists plugin's "List properties": numbering that starts, runs
    # backwards or jumps is the author's content, not formatting.
    "li": {"value"},
    "ol": {"start", "reversed"},
    "table": {"border"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    # ``dir``/``lang`` arrive with nearly every paste (Google Docs, Word,
    # ChatGPT) and TinyMCE keeps them: they set text direction and tell
    # screen readers and hyphenation which language a run is in.
    # ``title`` is the tooltip. All three are plain text and inert.
    # ``hidden`` too: the editor already hides that content from the
    # author, and dropping it would show readers text nobody reviewed.
    "*": {"class", "dir", "hidden", "id", "lang", "style", "title"},
}

# Every link's ``rel``: the value an editor typed is replaced, never kept.
# Set through ``set_tag_attribute_values`` rather than nh3's ``link_rel``,
# so the output is a fixed point. ``link_rel`` drops any ``rel`` and
# appends a new one, and ammonia removes an attribute the
# ``attribute_filter`` drops with ``swap_remove``, which moves that last
# attribute into the gap: ``<a class="MsoX" href>`` came out as
# ``<a rel href>`` and, on the next pass, ``<a href rel>``. An allowed
# ``rel`` is overwritten where it stands, so once placed it stays.
_SET_ATTRIBUTES = {"a": {"rel": "noopener noreferrer"}}


def is_allowed_embed_url(src: str) -> bool:
    """True when ``src`` is an absolute https URL on an embed origin.

    Compares the parsed origin, never a substring: ``youtube.com`` in
    ``https://youtube.com.evil.example/`` or in a query string must not
    pass. A ``netloc`` carrying userinfo (``https://www.youtube.com@evil``)
    cannot equal a listed origin either.
    """
    parts = urlsplit(src.strip())
    return f"{parts.scheme}://{parts.netloc.lower()}" in EMBED_IFRAME_ORIGINS


# Declarations a stored ``style`` never keeps:
# - a CSS custom property (``--name: ...``): an author never writes one.
#   It is the residue of a browser extension (Dark Reader's
#   ``--darkreader-inline-color``, on most of webside's blog posts) that
#   was running when the text was copied into the editor.
# - the typeface and the type size (``font``, ``font-family``,
#   ``font-size``): a store's typography is its theme's. Text pasted from
#   Word or a web page arrives as ``font-family: arial; font-size: 14pt``,
#   which drew webside's post 82 at 18.7 px against the store's 20 px and
#   downloads another face mid-render.
# - Word's ``mso-*`` properties, which no browser reads.
# Colour, alignment and spacing are the author's and stay.
_DROPPED_PROPERTY = re.compile(
    r"--[\w-]+|font|font-family|font-size|mso-[\w-]+", re.IGNORECASE
)

# Word's paragraph and comment classes (``MsoNormal``, ``msocomanchor``),
# in the two shapes Word writes them, so a store's own ``msonry`` stays.
_WORD_CLASS = re.compile(r"Mso[A-Z]\w*|msocom\w+")


def _without_comments(style: str) -> str:
    """``style`` without its CSS comments, which a browser skips anywhere
    between tokens: ``font-size/**/: 14pt`` is a type size, and a comment
    can hold a ``;`` or a quote that is neither. A ``/*`` inside a string
    is text. An unclosed comment runs to the end, as a browser reads it."""
    if "/*" not in style:
        return style
    kept: list[str] = []
    quote = ""
    index = 0
    while index < len(style):
        char = style[index]
        if quote:
            if char == "\\" and index + 1 < len(style):
                kept.append(style[index : index + 2])
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif style.startswith("/*", index):
            end = style.find("*/", index + 2)
            if end == -1:
                break
            index = end + 2
            continue
        kept.append(char)
        index += 1
    return "".join(kept)


def _declarations(style: str) -> list[str]:
    """The declarations of an inline ``style``, split on the semicolons
    that end one: not those inside a string or a ``url(...)``."""
    declarations: list[str] = []
    depth = 0
    quote = ""
    start = 0
    escaped = False
    for index, char in enumerate(style):
        if escaped:
            escaped = False
        elif quote:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            declarations.append(style[start:index])
            start = index + 1
    declarations.append(style[start:])
    return [d.strip() for d in declarations if d.strip()]


def _is_dropped(declaration: str) -> bool:
    """Whether a declaration sets a property a stored style never keeps.
    A name written with a CSS escape (``font-s\69ze``) is dropped too: no
    property needs one, and a browser reads it as the name it spells."""
    name = declaration.split(":", 1)[0].strip()
    return "\\" in name or bool(_DROPPED_PROPERTY.fullmatch(name))


def _clean_style(style: str) -> str | None:
    """``style`` without its comments and the declarations
    ``_DROPPED_PROPERTY`` names. A style with neither stays exactly as
    written; one left empty is dropped with its attribute."""
    bare = _without_comments(style)
    declarations = _declarations(bare)
    kept = [d for d in declarations if not _is_dropped(d)]
    if len(kept) == len(declarations) and bare == style:
        return style
    return f"{'; '.join(kept)};" if kept else None


def _clean_class(classes: str) -> str | None:
    """``classes`` without Word's; an attribute left empty is dropped."""
    names = classes.split()
    kept = [name for name in names if not _WORD_CLASS.fullmatch(name)]
    if len(kept) == len(names):
        return classes
    return " ".join(kept) or None


def _filter_attribute(tag: str, attr: str, value: str) -> str | None:
    # nh3 cannot drop an element from an attribute filter, so an iframe
    # pointing elsewhere keeps only its inert attributes and renders
    # ``about:blank``. Validation refuses it before it gets that far.
    if tag == "iframe" and attr == "src" and not is_allowed_embed_url(value):
        return None
    if attr == "style":
        return _clean_style(value)
    if attr == "class":
        return _clean_class(value)
    return value


_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
# Media that shows something only when it has something to load.
_SOURCE_ATTRIBUTES = {
    "img": "src",
    "iframe": "src",
    "embed": "src",
    "video": "src",
    "audio": "src",
    "source": "src",
    "object": "data",
}


class _VisibleContent(HTMLParser):
    """Whether any text or loadable media sits outside a hidden subtree."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible = False
        self._open: list[bool] = []

    @property
    def _hidden(self) -> bool:
        return any(self._open)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        hidden = "hidden" in attributes
        if not hidden and not self._hidden:
            source = _SOURCE_ATTRIBUTES.get(tag)
            if tag == "svg" or (
                source and (attributes.get(source) or "").strip()
            ):
                self.visible = True
        if tag not in _VOID_ELEMENTS:
            self._open.append(hidden)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self._open.pop()

    def handle_endtag(self, tag):
        if tag not in _VOID_ELEMENTS and self._open:
            self._open.pop()

    def handle_data(self, data):
        # ``data`` is already decoded (``convert_charrefs``), so a space
        # written ``&#x20;`` or ``&nbsp;`` is whitespace here.
        if not self._hidden and data.strip():
            self.visible = True


def has_visible_content(html: str | None) -> bool:
    """Whether ``html`` shows something: text, or media with a source.

    An editor saves an emptied field as ``""``, ``<p></p>`` or
    ``<p>&nbsp;</p>``, so a stored row is no evidence of a document. An
    element carrying the ``hidden`` attribute (the policy keeps it) shows
    nothing, nor does its content, and an image or frame with no source
    loads nothing. The same rule as the storefront's
    ``hasVisibleContent`` (``shared/utils/translations.ts``); change them
    together.
    """
    if not html:
        return False
    parser = _VisibleContent()
    parser.feed(html)
    parser.close()
    return parser.visible


def sanitize_html(html: str) -> str:
    """Return ``html`` reduced to the rich-text policy."""
    if not html:
        return html

    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_filter_attribute,
        set_tag_attribute_values=_SET_ATTRIBUTES,
        link_rel=None,
    )


_HEADING = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>\s*", re.DOTALL)


def drop_empty_headings(html: str) -> str:
    """Return ``html`` without the headings that show nothing.

    The editor leaves one behind when a heading's text is moved or
    deleted (``<h2 id="..."></h2>``). A reader sees a gap at most, but a
    screen reader announces a blank heading and a search engine reads a
    hole in the page's outline. A heading holding an image shows
    something and stays (``has_visible_content``).

    Separate from ``sanitize_html`` because ``lost_content`` refuses what
    that drops, and nothing is lost here.
    """
    return _HEADING.sub(
        lambda heading: (
            heading.group(0) if has_visible_content(heading.group(2)) else ""
        ),
        html,
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


# Attributes whose removal loses something a reader gets: where a link
# goes, what an image or player shows. Everything else nh3 drops is
# markup no reader sees - a paste's ``data-*``/``aria-*``/``role``, an
# event handler - and is normalised away without refusing the save.
_CONTENT_ATTRIBUTES = frozenset({"href", "src"})


def lost_content(html: str) -> list[str]:
    """The part of ``removed_markup`` a reader would miss, sorted.

    An element (``<video>``, ``<script>``) or a link/media address
    (``<a href>``, ``<iframe src>``). The other attributes the policy
    drops are editor or paste residue: TinyMCE keeps every attribute of
    pasted HTML, so refusing those would block a save over something
    nobody can see.
    """
    removed = removed_markup(html)
    lost = [
        key
        for key in removed
        if " " not in key or key[1:-1].split(" ", 1)[1] in _CONTENT_ATTRIBUTES
    ]
    # ``srcset`` alone is not content: an image pasted from a web page
    # carries it beside ``src`` and survives on ``src``. It is only when
    # an image is left with no ``src`` at all that the picture is gone.
    if "<img srcset>" in removed:
        kept = _markup(sanitize_html(html))
        if kept["<img>"] > kept["<img src>"]:
            lost.append("<img srcset>")
    return sorted(lost)
