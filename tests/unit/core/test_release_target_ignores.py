"""The release job and the workflow agree on which pushes start no run.

``.github/scripts/release_target.sh`` releases from the tip of ``main``
when every commit since the tested one started no CI run, which it
decides from the paths ``ci.yml`` ignores. A path added to
``paths-ignore`` and not to the script would leave a release waiting for
a run that never starts; one added only to the script would release a
tree no run tested.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import yaml
from semantic_release.const import DEFAULT_COMMIT_AUTHOR

_GITHUB = Path(__file__).resolve().parents[3] / ".github"
_SCRIPTS = _GITHUB / "scripts"
_SCRIPT = (_SCRIPTS / "release_target.sh").read_text(encoding="utf-8")
# The one definition of the ignored paths, sourced by release_target.sh and
# by the pull request change detection.
_IGNORED = (_SCRIPTS / "ignored_paths.sh").read_text(encoding="utf-8")


def _script_patterns() -> list[str]:
    declared = re.search(r"^IGNORED=\((.*)\)$", _IGNORED, re.MULTILINE)
    assert declared is not None
    return re.findall(r"'([^']+)'", declared.group(1))


def test_the_script_ignores_exactly_what_the_workflow_ignores():
    workflow = yaml.safe_load(
        (_GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    # YAML 1.1 reads the bare key ``on`` as the boolean True.
    triggers = workflow[True]

    assert triggers["push"]["paths-ignore"] == _script_patterns()
    # A pull request run starts on every change: a skipped workflow leaves a
    # required check "Pending". The ``changes`` job applies the same list.
    assert "paths-ignore" not in triggers["pull_request"]


def test_both_scripts_use_the_one_definition():
    for script in ("release_target.sh", "pr_touches_code.sh"):
        text = (_SCRIPTS / script).read_text(encoding="utf-8")
        assert "ignored_paths.sh" in text
        assert "IGNORED=(" not in text


def test_every_ignored_pattern_has_its_case_in_the_matcher():
    """``IGNORED`` is the declaration; the ``case`` is what runs."""
    matcher = re.search(
        r"ignored_path\(\) \{.*?case \$1 in\n\s*(.*?)\) return 0",
        _IGNORED,
        re.DOTALL,
    )
    assert matcher is not None
    cases = [case.strip() for case in matcher.group(1).split("|")]

    # ``case`` globs cross ``/`` by themselves, so ``**`` is written ``*``.
    assert cases == [
        pattern.replace("**", "*") for pattern in _script_patterns()
    ]


def test_the_script_knows_the_identity_semantic_release_commits_with():
    """Its version commit is recognised by author and committer too.

    The identity is semantic-release's default ``commit_author``; setting
    one in ``pyproject.toml`` or through ``GIT_COMMIT_AUTHOR`` in the
    workflow would make the script take every version commit for code.
    """
    declared = re.search(r"^RELEASE_BOT='([^']+)'$", _SCRIPT, re.MULTILINE)
    assert declared is not None

    assert declared.group(1) == DEFAULT_COMMIT_AUTHOR
    project = tomllib.loads(
        (_GITHUB.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert "commit_author" not in project["tool"]["semantic_release"]
    assert "GIT_COMMIT_AUTHOR" not in (
        _GITHUB / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")


_WORKFLOW = yaml.safe_load(
    (_GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
)
_JOBS = _WORKFLOW["jobs"]
_GATE_EXEMPT = {"changes", "tested-in-pr", "gate", "release"}


def test_the_gate_needs_every_job_that_tests_code():
    """Branch protection requires ``gate`` alone, so it must wait for all."""
    tested = set(_JOBS) - _GATE_EXEMPT
    assert set(_JOBS["gate"]["needs"]) == tested | {"changes", "tested-in-pr"}
    assert _JOBS["gate"]["if"] == "always()"


def test_the_gate_keeps_its_name():
    """Its name is the check context the repository settings require."""
    assert _JOBS["gate"]["name"] == "CI Gate"


def test_every_job_that_tests_code_waits_for_the_change_detection():
    # ``coverage`` needs ``testing``, so it is skipped with it.
    for name in set(_JOBS) - _GATE_EXEMPT - {"coverage"}:
        job = _JOBS[name]
        needs = job["needs"]
        needs = [needs] if isinstance(needs, str) else needs
        assert "changes" in needs, name
        assert "needs.changes.outputs.code == 'true'" in job["if"], name


def test_the_change_detection_runs_the_pinned_script():
    steps = _JOBS["changes"]["steps"]
    assert any("pr_touches_code.sh" in step.get("run", "") for step in steps)


def _detect(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which("bash") or "bash", str(_SCRIPTS / "pr_touches_code.sh")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo_with_change(tmp_path: Path, change) -> Path:
    """A base commit, then one commit made by ``change`` (the merge ref)."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "src" / "a.py").write_text("print('a')\n" * 20)
    (repo / "docs" / "a.md").write_text("a\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    change(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return repo


def test_a_docs_only_change_touches_no_code(tmp_path):
    repo = _repo_with_change(
        tmp_path, lambda r: (r / "docs" / "a.md").write_text("b\n")
    )
    result = _detect(repo)
    assert result.stdout.strip() == "code=false"


def test_a_change_to_code_and_docs_touches_code(tmp_path):
    def change(repo: Path) -> None:
        (repo / "docs" / "a.md").write_text("b\n")
        (repo / "src" / "a.py").write_text("print('b')\n" * 20)

    result = _detect(_repo_with_change(tmp_path, change))
    assert result.stdout.strip() == "code=true"


def test_moving_code_into_docs_touches_code(tmp_path):
    """The old path is listed too (``--no-renames``): a deletion of code."""

    def change(repo: Path) -> None:
        (repo / "src" / "a.py").rename(repo / "docs" / "a.py")

    result = _detect(_repo_with_change(tmp_path, change))
    assert result.stdout.strip() == "code=true"


def test_an_unreadable_diff_fails_instead_of_reporting_docs_only(tmp_path):
    result = _detect(tmp_path)
    assert result.returncode != 0
    assert "code=" not in result.stdout


def test_the_gate_fails_when_the_detection_gave_no_verdict():
    steps = _JOBS["gate"]["steps"]
    script = "".join(step.get("run", "") for step in steps)
    assert "needs.changes" not in script  # reads the env, not expressions
    assert "$CODE == true || $CODE == false" in script
    assert '"success" and .value.result != "skipped"' in script


def test_code_changed_then_reverted_leaves_only_docs(tmp_path):
    """The merge ref holds the net change: a reverted edit is no change."""
    repo = _repo_with_change(
        tmp_path, lambda r: (r / "docs" / "x.md").write_text("x\n")
    )
    # The pull request starts from the base commit (HEAD^1) ...
    _git(repo, "checkout", "-q", "-b", "pr", "HEAD^1")
    original = (repo / "src" / "a.py").read_text()
    (repo / "src" / "a.py").write_text("print('b')\n")
    _git(repo, "commit", "-q", "-am", "change code")
    (repo / "src" / "a.py").write_text(original)
    (repo / "docs" / "a.md").write_text("c\n")
    _git(repo, "commit", "-q", "-am", "revert the code, change docs")
    # ... and the merge ref is the pull request merged into the base.
    _git(repo, "checkout", "-q", "--detach", "pr~2")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "pr")

    assert _detect(repo).stdout.strip() == "code=false"
