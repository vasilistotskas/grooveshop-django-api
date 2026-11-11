"""The docs name files that exist, and nothing cites a private note.

Agents and people navigate this repository from ``CLAUDE.md``, the
``.claude/`` rules, skills and agents, and ``docs/``. A path in
backticks there is an instruction to open that file, so a path that no
longer exists sends the reader nowhere, and a moved module leaves one
behind unless the same change updates the page.

The second rule is about Claude memory notes (``project_*.md``,
``feedback_*.md``, ``reference_*.md``). They live in one person's home
directory and in no repository, so a doc or a comment that gives one as
the reason for a rule gives no reason at all to anyone else. It is read
in the docs and in every source file of ``_SOURCE_SUFFIXES``: a workflow
comment cited one unseen while only Python was read.

Neither rule has an allowlist: #561 fixed the last known failures.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_EXTENSION = r"\.(?:py|md|txt|html|yml|yaml|toml|json|sh|mjs|po|j2)"
# ``order/services.py``, ``order/services.py:120``, ``docs/x.md#anchor``
_REPO_PATH = re.compile(
    r"^(?P<path>[\w.-]+(?:/[\w.-]+)+" + _EXTENSION + r")(?:[:#][\w,:#-]*)?$"
)
# A file named without a folder, alone or followed by what is said about
# it: ``settings.py``, ``settings.py: REST_KNOX``, ``manage.py check``.
# The name has to be a root or source-root entry to count (see
# ``_first_party_roots``), which leaves ``models.py`` and ``conftest.py``
# out. ``settings.py`` moved into ``src/`` and the docs went on citing
# it by its bare name, unseen, because a path needed a slash.
_ROOT_FILE = re.compile(r"^(?P<path>[\w.-]+" + _EXTENSION + r")(?=$|[\s:#,(])")
# A template is named the way Django loads it, relative to this folder.
_TEMPLATE_ROOT = "src/core/templates"
# Directories whose entries are importable top-level packages.
_SOURCE_ROOTS = ("src",)
_MEMORY_NOTE = re.compile(r"\b(?:project|feedback|reference)_[a-z0-9_]+\.md\b")
_SOURCE_DIRS_SKIPPED = frozenset(
    {".venv", "node_modules", ".git", "migrations", "__pycache__", "htmlcov"}
)
_ORDER_DOC_MAX_CHARACTERS = 40_000
_ORDER_DOC_MAX_LINE = 200
# Code, workflows, hooks and config: where a comment gives a reason.
_SOURCE_SUFFIXES = frozenset({".py", ".yml", ".yaml", ".toml", ".sh", ".mjs"})


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
            token = token.strip()
            match = _REPO_PATH.match(token) or _ROOT_FILE.match(token)
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


def _source_files() -> list[Path]:
    """Every file of ``_SOURCE_SUFFIXES`` outside the skipped folders."""
    files = []
    for folder, subfolders, names in os.walk(REPO_ROOT):
        subfolders[:] = [
            name for name in subfolders if name not in _SOURCE_DIRS_SKIPPED
        ]
        files += [
            Path(folder, name)
            for name in names
            if Path(name).suffix in _SOURCE_SUFFIXES
        ]
    return files


def _memory_note_citations() -> set[str]:
    """``<file>: <note>`` for every memory note named in a tracked text."""
    files = _doc_files() + [
        path for path in _source_files() if path != Path(__file__).resolve()
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
    missing = sorted(_missing_paths())
    assert not missing, (
        "These docs name a file that does not exist. Fix the path, or "
        "move the doc together with the code:\n  " + "\n  ".join(missing)
    )


def test_nothing_cites_a_private_memory_note():
    cited = sorted(_memory_note_citations())
    assert not cited, (
        "A Claude memory note is in no repository. Write the reason "
        "itself, or the commit that made the rule:\n  " + "\n  ".join(cited)
    )


def test_a_memory_note_in_a_workflow_comment_is_reported(tmp_path, monkeypatch):
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "# stable per ``project_test_suite_stability.md``\n", encoding="utf-8"
    )
    vendored = tmp_path / ".venv" / "lib" / "notes.py"
    vendored.parent.mkdir(parents=True)
    vendored.write_text("# see feedback_someone_else.md\n", encoding="utf-8")
    monkeypatch.setattr("tests.unit.test_doc_references.REPO_ROOT", tmp_path)

    assert _memory_note_citations() == {
        ".github/workflows/ci.yml: project_test_suite_stability.md"
    }


def test_the_app_map_names_every_app():
    app_map = (REPO_ROOT / "docs" / "app-map.md").read_text(encoding="utf-8")
    apps = sorted(
        path.parent.name
        for source_dir in _SOURCE_ROOTS
        for path in (REPO_ROOT / source_dir).glob("*/apps.py")
    )
    assert len(apps) >= 30
    missing = [app for app in apps if f"`{app}/`" not in app_map]
    assert not missing, (
        "docs/app-map.md has no entry for these apps. Say what each owns, "
        "its entry points and the doc to read:\n  " + "\n  ".join(missing)
    )


def test_an_order_reference_file_fits_in_one_read():
    """``docs/order-system.md`` grew to 273,000 characters in one file,
    with paragraphs of up to 17,000 on one line, and could no longer be
    read whole. Split a file on a subject before it reaches the limit
    (``docs/order/index.md`` lists the files), and wrap prose: a table
    row is the only line that may be long."""
    too_big, too_long = [], []
    for path in sorted((REPO_ROOT / "docs" / "order").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if len(text) > _ORDER_DOC_MAX_CHARACTERS:
            too_big.append(f"{path.name}: {len(text)} characters")
        too_long += [
            f"{path.name}:{number}: {len(line)} characters"
            for number, line in enumerate(text.splitlines(), 1)
            if len(line) > _ORDER_DOC_MAX_LINE
            and not line.lstrip().startswith("|")
        ]
    assert not too_big, "\n  ".join(too_big)
    assert not too_long, "\n  ".join(too_long)


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


def test_a_root_file_moved_under_a_source_root_is_still_checked(
    tmp_path, monkeypatch
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").touch()
    (tmp_path / "manage.py").touch()
    (tmp_path / "CLAUDE.md").write_text(
        "Old `settings.py` and `settings.py: REST_KNOX`, new "
        "`src/settings.py`. `manage.py check` is at the root, and "
        "`models.py` names no root entry.",
        encoding="utf-8",
    )
    monkeypatch.setattr("tests.unit.test_doc_references.REPO_ROOT", tmp_path)

    assert _missing_paths() == {"CLAUDE.md: settings.py"}
