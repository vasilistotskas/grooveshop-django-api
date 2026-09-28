"""The admin says which deployment it is; the hardening is a separate
question.

Staging used to run ``SYSTEM_ENV=production`` so it would get the
production hardening, and the admin labelled it PRODUCTION: a red badge
and a ``[PROD]`` tab prefix on the environment built for rehearsing
changes. ``SYSTEM_ENV`` now names the deployment and
``settings.PRODUCTION_PROFILE`` carries the hardening, which staging
keeps.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import override_settings

from admin.environment import (
    environment_callback,
    environment_title_prefix_callback,
)

SETTINGS_FILE = Path(__file__).resolve().parents[3] / "settings.py"


@pytest.mark.parametrize(
    ("system_env", "badge", "prefix"),
    [
        ("production", ("Production", "danger"), "[PROD]"),
        ("staging", ("Staging", "warning"), "[STAGE]"),
        ("dev", ("Development", "success"), "[DEV]"),
        ("ci", ("CI", "info"), "[CI]"),
    ],
)
def test_the_admin_names_the_deployment(system_env, badge, prefix):
    with override_settings(SYSTEM_ENV=system_env):
        label, variant = environment_callback(None)

        assert (str(label), variant) == badge
        assert environment_title_prefix_callback(None) == prefix


def test_staging_runs_the_production_profile():
    """The rule itself, read from settings.py: a deployment that is not
    development or CI gets every production check."""
    source = SETTINGS_FILE.read_text(encoding="utf-8")

    assert (
        'PRODUCTION_PROFILE = SYSTEM_ENV in ("production", "staging")' in source
    )


def test_no_hardening_check_keys_on_the_deployment_name():
    """A check written ``SYSTEM_ENV == "production"`` would switch off on
    staging, which is exactly the drift staging exists to catch."""
    source = SETTINGS_FILE.read_text(encoding="utf-8")

    assert not re.search(r'SYSTEM_ENV\s*==\s*"production"', source)
