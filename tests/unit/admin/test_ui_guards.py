"""Static guards for the admin UI vocabulary.

The 2026-07 admin overhaul replaced every hand-rolled Tailwind pill,
emoji badge and inline style with unfold-native ``@display`` columns
(see ``admin/displays.py``). These guards keep it that way: a new
admin column must use the shared vocabulary, not reintroduce ad-hoc
markup that drifts from the theme and breaks dark mode.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

BASE_DIR = Path(settings.BASE_DIR)

ADMIN_FILES = (
    sorted(
        p
        for p in BASE_DIR.glob("*/admin.py")
        if ".venv" not in p.parts and "node_modules" not in p.parts
    )
    + sorted((BASE_DIR / "admin").rglob("*.py"))
    # Every admin template: pages are built from Unfold's components,
    # never from hand-rolled markup that drifts from the theme.
    + sorted((BASE_DIR / "core/templates/admin").rglob("*.html"))
    + sorted((BASE_DIR / "core/templates/unfold").rglob("*.html"))
)

# Pictographs, dingbats, transport, supplemental symbols — the emoji
# blocks that used to decorate status pills. Plain typography (arrows,
# em-dashes, Greek letters) is deliberately NOT banned.
# Codepoint ranges instead of a regex character class: CodeQL's regex
# parser reads astral ranges as surrogate pairs and reports
# py/overly-large-range on the class.
_EMOJI_RANGES = (
    (0x1F000, 0x1F0FF),
    (0x1F300, 0x1FAFF),
    (0x2460, 0x24FF),
    (0x2700, 0x27BF),
    (0x2B00, 0x2BFF),
)
_EMOJI_EXTRA = frozenset("✅❌⚠⭐❤")


def _first_emoji(line: str) -> str | None:
    for char in line:
        code = ord(char)
        if char in _EMOJI_EXTRA or any(
            lo <= code <= hi for lo, hi in _EMOJI_RANGES
        ):
            return char
    return None


_BANNED = {
    "inline style attribute": re.compile(r'style\s*=\s*["\']'),
    "inline <style> block (styles come from Unfold and the Tailwind build)": (
        re.compile(r"<style\b")
    ),
    "raw tailwind gray token (use base-*)": re.compile(r"text-gray-\d"),
    "hand-rolled pill markup": re.compile(r"rounded-full"),
    # Colours come from the theme (CSS variables, Unfold variants), so
    # dark mode recolours them; a literal only suits one theme.
    "hex colour literal": re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b"),
}


@pytest.mark.parametrize(
    "path", ADMIN_FILES, ids=lambda p: str(p.relative_to(BASE_DIR))
)
def test_admin_file_uses_unfold_vocabulary(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    emoji_hits = sorted(
        {
            f"line {lineno}: {char!r}"
            for lineno, line in enumerate(text.splitlines(), start=1)
            for char in [_first_emoji(line)]
            if char
        }
    )
    assert not emoji_hits, (
        f"{path.name} contains emoji — use unfold @display(label=...) "
        f"variants instead: {emoji_hits[:5]}"
    )

    for reason, pattern in _BANNED.items():
        hits = [
            f"line {lineno}"
            for lineno, line in enumerate(text.splitlines(), start=1)
            if pattern.search(line)
        ]
        assert not hits, f"{path.name}: {reason} at {hits[:5]}"


def test_both_admin_sites_load_the_project_stylesheet() -> None:
    """The admin templates use classes Unfold's stylesheet does not ship;
    the platform admin once rendered without ours, so its dashboard's
    breakpoint classes did not exist there."""
    from django.test import RequestFactory
    from unfold.settings import get_config

    request = RequestFactory().get("/admin/")
    for name in ("UNFOLD", "UNFOLD_PLATFORM"):
        styles = [str(style(request)) for style in get_config(name)["STYLES"]]
        assert any(style.endswith("css/styles.css") for style in styles), name


def test_the_admin_stylesheet_leaves_unfolds_icons_alone() -> None:
    """``static/css/admin.css`` is in no cascade layer, so each of its
    rules outranks Unfold's layered utilities. A ``display`` there on
    ``.material-symbols-outlined`` showed every icon Unfold hides with
    ``hidden``: the ⌘K palette's spinner turned forever."""
    css = (BASE_DIR / "static/css/admin.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert ".material-symbols-outlined" not in rules


def test_the_footer_api_docs_link_resolves_on_each_sites_urlconf() -> None:
    """The footer reverses ``api_docs_url_name`` on the site's own
    URLconf; a name missing there would 500 every page."""
    from django.urls import reverse

    from admin.admin import MyAdminSite
    from admin.platform_site import PlatformAdminSite

    assert reverse(MyAdminSite.api_docs_url_name, urlconf="core.urls")
    assert reverse(
        PlatformAdminSite.api_docs_url_name, urlconf="tenant.urls_public"
    )


_RESPONSIVE_COLUMNS = re.compile(r"\b((?:sm|md|lg|xl|2xl):grid-cols-\d+)\b")
_ONE_COLUMN = re.compile(r"(?<![\w:-])grid-cols-1(?![\w-])")


@pytest.mark.parametrize(
    "path", ADMIN_FILES, ids=lambda p: str(p.relative_to(BASE_DIR))
)
def test_a_responsive_grid_is_not_overridden_by_unfolds_stylesheet(
    path: Path,
) -> None:
    """Unfold's stylesheet loads AFTER ours (``unfold/layouts/
    skeleton.html``). A breakpoint class only our build carries loses to
    Unfold's plain ``.grid-cols-1`` - same layer, same specificity, later
    file - so a grid written ``grid-cols-1 sm:grid-cols-2`` stays one
    column at every width. Leave ``grid-cols-1`` out (a grid is one
    column without it)."""
    import unfold

    unfold_css = (
        Path(unfold.__file__).parent / "static/unfold/css/styles.css"
    ).read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not _ONE_COLUMN.search(line):
            continue
        for variant in _RESPONSIVE_COLUMNS.findall(line):
            escaped = "." + variant.replace(":", "\\:") + "{"
            if escaped not in unfold_css:
                hits.append(f"line {lineno}: {variant}")
    assert not hits, f"{path.name}: overridden by .grid-cols-1 at {hits}"
