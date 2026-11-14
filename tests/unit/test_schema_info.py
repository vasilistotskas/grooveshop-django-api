"""The OpenAPI document's ``info`` block is the same on every machine.

``schema.yml`` is committed and is the API contract the storefront and the
agent gateway generate from. Its ``title`` and ``description`` once came from
environment variables, so a developer with a different ``.env`` regenerated a
different file and CI compared against yet another. They are constants now;
these tests pin that, and that the committed file carries exactly what the
code generates.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = ROOT / "schema.yml"
SETTINGS_FILE = ROOT / "src" / "settings" / "api.py"


def test_the_committed_schema_info_is_what_the_code_generates(openapi_schema):
    committed = yaml.safe_load(SCHEMA_FILE.read_text(encoding="utf-8"))

    assert committed["info"] == openapi_schema["info"]


def test_the_info_is_written_in_settings_not_read_from_the_environment():
    """``TITLE`` and ``DESCRIPTION`` are literals in ``settings/api.py``: a call
    (``getenv`` and the like) would make the generated file depend on the
    machine it is generated on."""
    tree = ast.parse(SETTINGS_FILE.read_text(encoding="utf-8"))
    spectacular = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SPECTACULAR_SETTINGS"
            for target in node.targets
        )
    )
    assert isinstance(spectacular, ast.Dict)
    entries = {
        key.value: value
        for key, value in zip(spectacular.keys, spectacular.values, strict=True)
        if isinstance(key, ast.Constant)
    }

    for name in ("TITLE", "DESCRIPTION", "VERSION"):
        assert isinstance(entries[name], ast.Constant), name
