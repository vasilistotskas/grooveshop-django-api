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
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002700-\U000027bf"
    "\U0001f000-\U0001f0ff\U00002b00-\U00002bff"
    "\U00002460-\U000024ff✅❌⚠⭐❤]"
)

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
            f"line {lineno}: {match.group(0)!r}"
            for lineno, line in enumerate(text.splitlines(), start=1)
            for match in [_EMOJI.search(line)]
            if match
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
