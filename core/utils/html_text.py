"""The text a reader sees in a piece of HTML."""

from __future__ import annotations

from html.parser import HTMLParser


class _VisibleText(HTMLParser):
    """Collects text, with a space where any tag was.

    A space at every tag boundary, so ``<p>a</p><p>b</p>`` reads
    ``a b`` and not the one word ``ab`` that ``strip_tags`` makes of it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        self.parts.append(" ")

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        self.parts.append(" ")


def visible_text(html: str | None) -> str | None:
    """``html`` as the single-spaced text it shows, entities decoded.

    What a search index should hold of a rich-text field: the words, not
    the tag and attribute text (``src`` paths, ``style``, ``class``,
    ``data-*``) around them. ``None`` stays ``None``.
    """
    if html is None:
        return None
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return " ".join("".join(parser.parts).split())
