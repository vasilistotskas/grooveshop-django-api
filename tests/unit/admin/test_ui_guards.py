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

ADMIN_FILES = sorted(
    p
    for p in BASE_DIR.glob("*/admin.py")
    if ".venv" not in p.parts and "node_modules" not in p.parts
) + sorted((BASE_DIR / "admin").glob("*.py"))

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
    "raw tailwind gray token (use base-*)": re.compile(r"text-gray-\d"),
    "hand-rolled pill markup": re.compile(r"rounded-full"),
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
