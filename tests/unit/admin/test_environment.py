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

import os
import re
import subprocess
import sys
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


def _import_settings(**env):
    """Import settings.py in a fresh interpreter: its checks run at
    import time, which an in-process test cannot repeat."""
    return subprocess.run(
        [sys.executable, "-c", "import settings"],
        cwd=SETTINGS_FILE.parent,
        env={
            **os.environ,
            "DB_PASSWORD": "not-the-default",
            "SECRET_KEY": "not-the-default",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer"])
def test_the_production_profile_refuses_a_cleartext_database(sslmode):
    """``require`` is only the default; an explicit weaker DB_SSLMODE
    must not downgrade production or staging to cleartext."""
    result = _import_settings(SYSTEM_ENV="staging", DB_SSLMODE=sslmode)

    assert result.returncode != 0
    assert f"DB_SSLMODE='{sslmode}' allows an unencrypted" in result.stderr


def test_the_production_profile_accepts_a_tls_database():
    result = _import_settings(SYSTEM_ENV="staging", DB_SSLMODE="verify-full")

    assert result.returncode == 0, result.stderr
