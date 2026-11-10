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
from pathlib import Path

import yaml

_GITHUB = Path(__file__).resolve().parents[3] / ".github"
_SCRIPT = (_GITHUB / "scripts" / "release_target.sh").read_text(
    encoding="utf-8"
)


def _script_patterns() -> list[str]:
    declared = re.search(r"^IGNORED=\((.*)\)$", _SCRIPT, re.MULTILINE)
    assert declared is not None
    return re.findall(r"'([^']+)'", declared.group(1))


def test_the_script_ignores_exactly_what_the_workflow_ignores():
    workflow = yaml.safe_load(
        (_GITHUB / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    # YAML 1.1 reads the bare key ``on`` as the boolean True.
    triggers = workflow[True]

    assert triggers["push"]["paths-ignore"] == _script_patterns()
    assert triggers["pull_request"]["paths-ignore"] == _script_patterns()


def test_every_ignored_pattern_has_its_case_in_the_matcher():
    """``IGNORED`` is the declaration; the ``case`` is what runs."""
    matcher = re.search(
        r"ignored_path\(\) \{.*?case \$1 in\n\s*(.*?)\) return 0",
        _SCRIPT,
        re.DOTALL,
    )
    assert matcher is not None
    cases = [case.strip() for case in matcher.group(1).split("|")]

    # ``case`` globs cross ``/`` by themselves, so ``**`` is written ``*``.
    assert cases == [
        pattern.replace("**", "*") for pattern in _script_patterns()
    ]
