"""The docs name files that exist, and nothing cites a private note.

Agents and people navigate this repository from ``CLAUDE.md``, the
``.claude/`` rules, skills and agents, and ``docs/``. A path in
backticks there is an instruction to open that file, so a path that no
longer exists sends the reader nowhere, and a moved module leaves one
behind unless the same change updates the page.

The second rule is about Claude memory notes (``project_*.md``,
``feedback_*.md``, ``reference_*.md``). They live in one person's home
directory and in no repository, so a doc or a comment that gives one as
the reason for a rule gives no reason at all to anyone else.

Both rules start with the failures known on the day they were written,
listed in ``doc_reference_exceptions.txt`` with the issue that removes
them. The list only shrinks: an entry that no longer fails must be
deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXCEPTIONS_FILE = Path(__file__).with_name("doc_reference_exceptions.txt")

_BACKTICKED = re.compile(r"`([^`\n]+)`")
# ``order/services.py``, ``order/services.py:120``, ``docs/x.md#anchor``
_REPO_PATH = re.compile(
    r"^(?P<path>[\w.-]+(?:/[\w.-]+)+\.(?:py|md|txt|html|yml|yaml|toml|json|sh|mjs|po|j2))"
    r"(?:[:#][\w,:#-]*)?$"
)
# A template is named the way Django loads it, relative to this folder.
_TEMPLATE_ROOT = "core/templates"
# Directories whose entries are importable top-level packages.
_SOURCE_ROOTS = ("src",)
_MEMORY_NOTE = re.compile(r"\b(?:project|feedback|reference)_[a-z0-9_]+\.md\b")
_SOURCE_DIRS_SKIPPED = frozenset(
    {".venv", "node_modules", ".git", "migrations", "__pycache__", "htmlcov"}
)


def _doc_files() -> list[Path]:
    files = [REPO_ROOT / "CLAUDE.md", REPO_ROOT / "README.md"]
    files += sorted((REPO_ROOT / "docs").rglob("*.md"))
    files += sorted((REPO_ROOT / ".claude").rglob("*.md"))
    return [path for path in files if path.is_file()]


def _first_party_roots() -> frozenset[str]:
    """First segments that mean "a path of this repository".

    The entries at the repository root, and the entries of a source
    directory under it. The second set is what keeps a moved package
    checked: once ``order/`` lives in ``src/``, a doc still saying
    ``order/services.py`` names a first-party path that does not exist,
    and is reported rather than taken for another repository's.
    """
    roots = {path.name for path in REPO_ROOT.iterdir()}
    for source_dir in _SOURCE_ROOTS:
        if (REPO_ROOT / source_dir).is_dir():
            roots.update(
                path.name for path in (REPO_ROOT / source_dir).iterdir()
            )
    return frozenset(roots)


def _exceptions() -> set[str]:
    return {
        line.strip()
        for line in EXCEPTIONS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _missing_paths() -> set[str]:
    """``<doc>: <path>`` for every backticked repo path that is absent.

    A token counts as a path of this repository when its first segment
    is one of ``_first_party_roots``, which leaves out the
    sibling repositories' paths (``app/components/...``) that the docs
    also mention.
    """
    roots = _first_party_roots()
    missing = set()
    for doc in _doc_files():
        rel_doc = doc.relative_to(REPO_ROOT).as_posix()
        for token in _BACKTICKED.findall(doc.read_text(encoding="utf-8")):
            match = _REPO_PATH.match(token.strip())
            if match is None:
                continue
            path = match["path"]
            if path.split("/")[0] not in roots:
                continue
            if (REPO_ROOT / path).exists():
                continue
            if (REPO_ROOT / _TEMPLATE_ROOT / path).exists():
                continue
            missing.add(f"{rel_doc}: {path}")
    return missing


def _memory_note_citations() -> set[str]:
    """``<file>: <note>`` for every memory note named in a tracked text."""
    files = _doc_files() + [
        path
        for path in REPO_ROOT.rglob("*.py")
        if not _SOURCE_DIRS_SKIPPED & set(path.relative_to(REPO_ROOT).parts)
        and path != Path(__file__).resolve()
    ]
    found = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        found.update(f"{rel}: {note}" for note in _MEMORY_NOTE.findall(text))
    return found


def test_the_scan_reads_the_docs():
    assert len(_doc_files()) >= 20
    assert "tests" in _first_party_roots()


def test_every_backticked_repo_path_exists():
    new = sorted(_missing_paths() - _exceptions())
    assert not new, (
        "These docs name a file that does not exist. Fix the path, or "
        "move the doc together with the code:\n  " + "\n  ".join(new)
    )


def test_nothing_cites_a_private_memory_note():
    new = sorted(_memory_note_citations() - _exceptions())
    assert not new, (
        "A Claude memory note is in no repository. Write the reason "
        "itself, or the commit that made the rule:\n  " + "\n  ".join(new)
    )


def test_the_exception_list_only_shrinks():
    stale = sorted(_exceptions() - _missing_paths() - _memory_note_citations())
    assert not stale, (
        f"These entries of {EXCEPTIONS_FILE.name} no longer fail. Delete "
        "them:\n  " + "\n  ".join(stale)
    )


def test_a_package_moved_under_a_source_root_is_still_checked(
    tmp_path, monkeypatch
):
    (tmp_path / "src" / "order").mkdir(parents=True)
    (tmp_path / "src" / "order" / "services.py").touch()
    (tmp_path / "CLAUDE.md").write_text(
        "Old `order/services.py`, new `src/order/services.py`, "
        "and the storefront's `app/pages/index.vue`.",
        encoding="utf-8",
    )
    monkeypatch.setattr("tests.unit.test_doc_references.REPO_ROOT", tmp_path)

    assert _missing_paths() == {"CLAUDE.md: order/services.py"}
