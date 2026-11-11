"""The admin's ⌘K palette: pages first, then a few records of each kind.

Unfold's own ``search`` matched a term against Django's app list by app
name as well as model name, so typing "orders" listed every model of the
orders app - history tables, counters and webhook logs - before the
Orders page itself. It compared accents exactly ("παραγγελιες" found
nothing), listed the screens of features a store does not have, and read
every match of every model with no limit (about a second per answer).

This keeps what the palette is for - reaching any screen the viewer can
open, including the ones the sidebar leaves out, and any record on them
- and orders it:

1. the entries of this site's sidebar (``get_sidebar_list``: the same
   per-request menu, permissions applied), compared without case or
   accents and ranked - the title itself, a title starting with the term,
   a word starting with it, the term anywhere, and last every entry of a
   section whose name matches;
2. the other screens the viewer can open, by their own name only;
3. up to ``RECORDS_PER_MODEL`` records of each of those models, through
   the model's own admin search and ordering, the sidebar's models first,
   with a link to the full results when there are more.

Screens ``command_hidden`` names - on a store, the apps of a feature it
does not have and the pages its settings switch off - are left out of 2
and 3, as the sidebar leaves them out; a page the sidebar still lists
stays searchable.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.text import capfirst
from django.utils.translation import gettext as _
from django.utils.translation import pgettext
from unfold.dataclasses import SearchResult
from unfold.settings import get_config

#: Records listed per model; one more is read to know there are more.
RECORDS_PER_MODEL = 5

#: A shorter term matches pages only: one letter matches every record.
MIN_RECORD_TERM = 2

#: The icon of a screen the sidebar does not list (Unfold's own).
SCREEN_ICON = "tag"

# Page ranks, best first. A section match lists the whole section.
_EXACT, _PREFIX, _WORD_PREFIX, _ANYWHERE, _SECTION = range(5)


def fold(text: str) -> str:
    """``text`` without case or accents, so "Παραγγελίες" matches
    "παραγγελιες" (and final "ς" matches "σ")."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _rank(title: str, term: str) -> int | None:
    title = fold(title)
    if title == term:
        return _EXACT
    if title.startswith(term):
        return _PREFIX
    if any(word.startswith(term) for word in title.split()):
        return _WORD_PREFIX
    if term in title:
        return _ANYWHERE
    return None


def _path(link: str) -> str:
    return link.split("?")[0]


@dataclass(frozen=True)
class Screen:
    """A model the viewer can open, from Django's app list."""

    model: Any
    title: str
    app: str
    link: str


def _sidebar_entries(
    sidebar: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], str, tuple[str, ...]]]:
    """Every page of the sidebar the viewer may open, as (entry, where
    it sits, the titles of what holds it: its section and sub-menu). A
    sub-menu yields its pages, not itself."""
    for group in sidebar:
        section = str(group.get("title") or "")
        for item in group["items"]:
            if not item.get("has_permission", True):
                continue
            children = [
                child
                for child in item.get("items", [])
                if child.get("has_permission", True)
            ]
            if not children:
                yield item, section, (section,)
                continue
            menu = str(item["title"])
            for child in children:
                # A sub-menu's first page often shares its title.
                path = (section,) if child["title"] == menu else (section, menu)
                yield (
                    {"icon": item.get("icon"), **child},
                    " › ".join(filter(None, path)),
                    (section, menu),
                )


def _link(entry: dict[str, Any]) -> str:
    return str(entry.get("link_callback") or entry["link"])


def sidebar_icons(sidebar: list[dict[str, Any]]) -> dict[str, str]:
    """Each page the sidebar lists (its address without a query), in
    sidebar order, with the icon it has there ("" for none)."""
    icons: dict[str, str] = {}
    for entry, *_where in _sidebar_entries(sidebar):
        icons.setdefault(_path(_link(entry)), entry.get("icon") or "")
    return icons


def page_results(
    sidebar: list[dict[str, Any]], screens: list[Screen], term: str
) -> list[SearchResult]:
    """Sidebar pages, then other screens, matching ``term``; best first."""
    term = fold(term)
    ranked: dict[str, tuple[int, int, int, SearchResult]] = {}

    def offer(tier, rank, order, link, result):
        if rank is not None and (
            link not in ranked or (tier, rank) < ranked[link][:2]
        ):
            ranked[link] = (tier, rank, order, result)

    entries = list(_sidebar_entries(sidebar))
    for order, (entry, where, holders) in enumerate(entries):
        rank = _rank(str(entry["title"]), term)
        if rank is None and any(term in fold(title) for title in holders):
            rank = _SECTION
        link = _link(entry)
        result = SearchResult(
            title=str(entry["title"]),
            description=where or pgettext("navigation link", "Home"),
            link=link,
            icon=entry.get("icon"),
        )
        offer(0, rank, order, link, result)

    listed = {_path(_link(entry)) for entry, *_where in entries}
    for order, screen in enumerate(screens):
        if screen.link in listed:
            continue
        result = SearchResult(
            title=screen.title,
            description=screen.app,
            link=screen.link,
            icon=SCREEN_ICON,
        )
        offer(1, _rank(screen.title, term), order, screen.link, result)

    return [result for *_rank, result in sorted(ranked.values())]


def record_results(
    site: Any,
    request: HttpRequest,
    term: str,
    screens: list[Screen],
    icons: dict[str, str],
) -> list[SearchResult]:
    """Up to ``RECORDS_PER_MODEL`` matching records of each screen, the
    sidebar's first, each under the icon its page has there."""
    results = []
    in_sidebar = list(icons)
    ordered = sorted(
        screens,
        key=lambda s: (
            in_sidebar.index(s.link) if s.link in icons else len(icons)
        ),
    )
    for screen in ordered:
        model_admin = site._registry[screen.model]
        if not model_admin.get_search_fields(request):
            continue
        queryset, may_have_duplicates = model_admin.get_search_results(
            request, model_admin.get_queryset(request), term
        )
        if may_have_duplicates:
            queryset = queryset.distinct()
        ordering = model_admin.get_ordering(request) or ("-pk",)
        found = list(queryset.order_by(*ordering)[: RECORDS_PER_MODEL + 1])
        if not found:
            continue

        opts = screen.model._meta
        change = f"{site.name}:{opts.app_label}_{opts.model_name}_change"
        icon = icons.get(screen.link) or "data_object"
        results.extend(
            SearchResult(
                title=str(obj),
                description=capfirst(opts.verbose_name),
                link=reverse(change, args=[obj.pk]),
                icon=icon,
            )
            for obj in found[:RECORDS_PER_MODEL]
        )
        if len(found) > RECORDS_PER_MODEL:
            results.append(
                SearchResult(
                    title=_("View all"),
                    description=capfirst(opts.verbose_name_plural),
                    link=f"{screen.link}?{urlencode({'q': term})}",
                    icon="search",
                )
            )
    return results


class CommandSearchMixin:
    """Answer the ⌘K palette as the module docstring describes.

    Renders Unfold's own results template, so the palette's markup and
    keyboard handling stay Unfold's; only what is found changes.
    """

    def command_hidden(self, request: HttpRequest) -> frozenset[str]:
        """App labels ("blog") and model labels ("blog.BlogComment") the
        palette leaves out, unless this site's sidebar lists the page."""
        return frozenset()

    def _command_screens(
        self, request: HttpRequest, listed: set[str]
    ) -> list[Screen]:
        hidden = self.command_hidden(request)
        # Django's app list: every model this viewer may open, by app.
        return [
            Screen(
                model=model["model"],
                title=str(model["name"]),
                app=str(app["name"]),
                link=model["admin_url"],
            )
            for app in self.get_app_list(request)
            for model in app["models"]
            if model.get("admin_url")
            and (
                model["admin_url"] in listed
                or not {app["app_label"], model["model"]._meta.label} & hidden
            )
        ]

    def search(self, request, extra_context=None):
        started = time.perf_counter()
        term = request.GET.get("s", "").strip()
        if not term:
            return HttpResponse()

        sidebar = self.get_sidebar_list(request)
        icons = sidebar_icons(sidebar)
        screens = self._command_screens(request, set(icons))
        results = page_results(sidebar, screens, term)
        if len(term) >= MIN_RECORD_TERM:
            results += record_results(self, request, term, screens, icons)

        paginator = Paginator(results, max(len(results), 1))
        command = get_config(self.settings_name)["COMMAND"]
        return TemplateResponse(
            request,
            "unfold/helpers/command_results.html",
            {
                "search_term": term,
                "page_obj": paginator,
                "results": paginator.page(1),
                "page_counter": 0,
                "execution_time": time.perf_counter() - started,
                "command_show_history": command.get("show_history"),
            },
            headers={"HX-Trigger": "search"},
        )
