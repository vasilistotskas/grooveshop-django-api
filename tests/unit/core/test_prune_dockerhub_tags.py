"""Which published image tags the registry cleanup is allowed to delete.

Every release pushes a tag and nothing ever removed one, so
``gro0ve/grooveshop-django-api`` reached 718 tags going back ~1160 days
— ~111.5 GB of it prunable. The cleanup workflow fixes that, and a bug
in the rule below deletes published artifacts permanently: a tag cannot
be restored, and rebuilding an old one is not guaranteed to reproduce
once dependencies have drifted.

So the selection is a plain function with no I/O, and these tests pin
the three things that keep it safe — the newest N survive, recent tags
survive regardless of N, and ``latest`` always survives.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "scripts"
    / "prune_dockerhub_tags.py"
)


def _load():
    """The script lives outside the package, so import it by path."""
    spec = importlib.util.spec_from_file_location("prune_tags", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prune = _load()

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def tag(name: str, days_old: int, size: int = 240_000_000) -> dict:
    stamp = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"name": name, "last_updated": stamp, "full_size": size}


class TestWhatSurvives:
    def test_the_newest_tags_are_kept_however_old_they_are(self):
        """A repo that stopped releasing a year ago still keeps its most
        recent releases — otherwise a quiet period wipes the rollback
        target."""
        tags = [tag(f"v{i}", days_old=400 + i) for i in range(10)]

        doomed = prune.select_doomed(tags, keep=5, min_age_days=90, now=NOW)

        assert {t["name"] for t in doomed} == {"v5", "v6", "v7", "v8", "v9"}

    def test_recent_tags_survive_even_beyond_the_keep_count(self):
        """The age floor is the second rule, not a tiebreak. Ten releases
        in a week must not push last week's live tag off a cliff."""
        tags = [tag(f"v{i}", days_old=i) for i in range(20)]

        doomed = prune.select_doomed(tags, keep=5, min_age_days=90, now=NOW)

        assert doomed == []

    def test_latest_is_never_deleted(self):
        tags = [tag("latest", days_old=900)] + [
            tag(f"v{i}", days_old=900 + i) for i in range(10)
        ]

        doomed = prune.select_doomed(tags, keep=0, min_age_days=90, now=NOW)

        assert "latest" not in {t["name"] for t in doomed}

    def test_a_tag_on_the_age_boundary_is_kept(self):
        """Strictly older than the cutoff, so exactly-90-days survives."""
        tags = [tag(f"filler{i}", days_old=500) for i in range(5)]
        tags.append(tag("boundary", days_old=90))

        doomed = prune.select_doomed(tags, keep=0, min_age_days=90, now=NOW)

        assert "boundary" not in {t["name"] for t in doomed}


class TestWhatGoes:
    def test_old_surplus_tags_are_selected(self):
        tags = [tag(f"v{i}", days_old=200 + i) for i in range(10)]

        doomed = prune.select_doomed(tags, keep=3, min_age_days=90, now=NOW)

        assert len(doomed) == 7

    def test_ordering_is_by_date_not_by_name(self):
        """Tag names sort lexically, so v10 < v9 as a string. Sorting on
        the name would keep the wrong releases."""
        tags = [
            tag("v9", days_old=400),
            tag("v10", days_old=1),
            tag("v11", days_old=2),
        ]

        doomed = prune.select_doomed(tags, keep=2, min_age_days=90, now=NOW)

        assert [t["name"] for t in doomed] == ["v9"]

    def test_an_empty_repo_is_not_an_error(self):
        assert prune.select_doomed([], keep=100, min_age_days=90, now=NOW) == []


class TestMalformedInput:
    @pytest.mark.parametrize(
        "bad", [{}, {"name": "x"}, {"name": "x", "last_updated": None}]
    )
    def test_a_tag_without_a_usable_date_is_treated_as_ancient(self, bad):
        """The API has never omitted last_updated, but guessing "now" for
        a missing date would protect junk forever."""
        tags = [tag(f"v{i}", days_old=1) for i in range(5)] + [bad]

        doomed = prune.select_doomed(tags, keep=5, min_age_days=90, now=NOW)

        assert len(doomed) == 1
