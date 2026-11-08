"""Every place that names the Python version names the same one.

``.python-version`` is what uv and CI's ``setup-python`` read. The
Dockerfiles cannot read it, because an ``ARG`` before the first ``FROM``
is fixed text, so they repeat it. Their builders set
``UV_PYTHON_DOWNLOADS=0``, so a mismatch fails the image build, but only
in the release workflow, after the merge. This test fails it in the PR.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _python_version() -> str:
    return (ROOT / ".python-version").read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "dev.Dockerfile"])
def test_the_images_build_on_the_python_version_file(dockerfile):
    text = (ROOT / dockerfile).read_text(encoding="utf-8")

    [pinned] = re.findall(r"^ARG PYTHON_VERSION=(\S+)$", text, re.MULTILINE)

    assert pinned == _python_version()


def test_the_tools_target_the_same_minor_version():
    major, minor = _python_version().split(".")[:2]
    config = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert config["project"]["requires-python"] == f">={major}.{minor}"
    assert config["tool"]["ruff"]["target-version"] == f"py{major}{minor}"
    assert (
        config["tool"]["ty"]["environment"]["python-version"]
        == f"{major}.{minor}"
    )
