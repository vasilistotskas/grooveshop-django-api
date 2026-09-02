from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management.base import CommandError
from django.test import override_settings

from core.management.commands.backup_database import Command


def _run_command(**overrides):
    command = Command()
    command.stdout = StringIO()
    command.stderr = StringIO()
    options = {
        "output_dir": "backups",
        "filename": "unit_backup",
        "compress": False,
        "format": "custom",
    }
    options.update(overrides)
    command.handle(**options)
    return command


class TestBackupDatabaseCommand:
    """The failure path must not leave a fake backup behind.

    ``pg_dump --file`` creates its output before connecting, so an
    aborted dump (server/client major mismatch, auth failure) leaves a
    zero-byte file that a directory listing cannot tell from a real
    backup and that ``cleanup_old_backups`` counts inside the retention
    window. Production accumulated eight such files and zero valid
    scheduled backups before this was caught (2026-09-02).
    """

    def test_failed_pg_dump_raises_and_discards_partial_file(
        self, tmp_path: Path
    ):
        def failing_pg_dump(cmd, **_kwargs):
            # Mirror pg_dump: the --file target exists, empty, then abort.
            target = next(
                a for a in cmd if a.startswith("--file=")
            ).removeprefix("--file=")
            Path(target).touch()
            return subprocess.CompletedProcess(
                cmd,
                returncode=1,
                stdout=b"",
                stderr=b"pg_dump: error: aborting because of server version mismatch",
            )

        with (
            override_settings(BASE_DIR=tmp_path),
            patch(
                "core.management.commands.backup_database.subprocess.run",
                side_effect=failing_pg_dump,
            ),
            pytest.raises(CommandError, match="server version mismatch"),
        ):
            _run_command()

        assert list((tmp_path / "backups").iterdir()) == []

    def test_empty_output_is_rejected_and_discarded(self, tmp_path: Path):
        def silent_pg_dump(cmd, **_kwargs):
            target = next(
                a for a in cmd if a.startswith("--file=")
            ).removeprefix("--file=")
            Path(target).touch()
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=b"", stderr=b""
            )

        with (
            override_settings(BASE_DIR=tmp_path),
            patch(
                "core.management.commands.backup_database.subprocess.run",
                side_effect=silent_pg_dump,
            ),
            pytest.raises(CommandError, match="not created or is empty"),
        ):
            _run_command()

        assert list((tmp_path / "backups").iterdir()) == []

    def test_successful_dump_is_kept(self, tmp_path: Path):
        def working_pg_dump(cmd, **_kwargs):
            target = next(
                a for a in cmd if a.startswith("--file=")
            ).removeprefix("--file=")
            Path(target).write_bytes(b"PGDMP")
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=b"", stderr=b""
            )

        with (
            override_settings(BASE_DIR=tmp_path),
            patch(
                "core.management.commands.backup_database.subprocess.run",
                side_effect=working_pg_dump,
            ),
        ):
            command = _run_command()

        assert (
            tmp_path / "backups" / "unit_backup.dump"
        ).read_bytes() == b"PGDMP"
        assert "completed successfully" in command.stdout.getvalue()
