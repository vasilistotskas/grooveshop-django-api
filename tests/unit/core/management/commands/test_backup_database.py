from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management.base import CommandError
from django.test import override_settings

from core.management.commands.backup_database import (
    PG_DUMP_TIMEOUT_SECONDS,
    Command,
)


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


class TestEveryFormatGoesThroughOneUnpipedPath:
    """No format may pipe pg_dump's stdout into another process.

    The plain+compress path used to: pg_dump's stdout fed `gzip` while
    its stderr went to a PIPE that nothing drained until AFTER
    `gzip.communicate()` returned — and that call cannot return until
    pg_dump closes stdout. `--verbose` fills the stderr buffer, pg_dump
    blocks writing it, gzip waits for input that never comes. Reproduced
    with the identical process shape: 4 KiB of stderr completes, 8 KiB
    hangs. That branch also carried no timeout, so it hung indefinitely
    rather than raising `TimeoutExpired`.

    pg_dump writes and compresses the file itself, so the fix is that
    there is nothing left to pipe.
    """

    def test_plain_and_compressed_asks_pg_dump_to_write_the_gzip(
        self, tmp_path: Path
    ):
        captured: list[list[str]] = []

        def working_pg_dump(cmd, **_kwargs):
            captured.append(cmd)
            target = next(
                a for a in cmd if a.startswith("--file=")
            ).removeprefix("--file=")
            Path(target).write_bytes(b"\x1f\x8b")
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=b"", stderr=b""
            )

        with (
            override_settings(BASE_DIR=tmp_path),
            patch(
                "core.management.commands.backup_database.subprocess.run",
                side_effect=working_pg_dump,
            ),
            patch(
                "core.management.commands.backup_database.subprocess.Popen"
            ) as popen,
        ):
            _run_command(format="plain", compress=True)

        assert not popen.called, "still piping pg_dump into another process"
        (cmd,) = captured
        assert "--compress=gzip" in cmd
        assert f"--file={tmp_path / 'backups' / 'unit_backup.sql.gz'}" in cmd

    @pytest.mark.parametrize(
        ("fmt", "compress", "extension"),
        [
            ("custom", False, ".dump"),
            ("tar", False, ".tar"),
            ("plain", False, ".sql"),
            ("plain", True, ".sql.gz"),
        ],
    )
    def test_every_format_writes_through_file_with_a_timeout(
        self, tmp_path: Path, fmt: str, compress: bool, extension: str
    ):
        captured: list[dict] = []

        def working_pg_dump(cmd, **kwargs):
            captured.append({"cmd": cmd, **kwargs})
            target = next(
                a for a in cmd if a.startswith("--file=")
            ).removeprefix("--file=")
            Path(target).write_bytes(b"X")
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
            _run_command(format=fmt, compress=compress)

        (call,) = captured
        # A dump that stops making progress has to fail the task rather
        # than pin a worker; the pipe path had no timeout at all.
        assert call["timeout"] == PG_DUMP_TIMEOUT_SECONDS
        assert any(a.startswith("--file=") for a in call["cmd"])
        assert (tmp_path / "backups" / f"unit_backup{extension}").exists()

    def test_only_the_compressed_plain_format_asks_for_compression(
        self, tmp_path: Path
    ):
        """`--compress` on a custom dump would be a second compression."""
        captured: list[list[str]] = []

        def working_pg_dump(cmd, **_kwargs):
            captured.append(cmd)
            target = next(
                a for a in cmd if a.startswith("--file=")
            ).removeprefix("--file=")
            Path(target).write_bytes(b"X")
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
            _run_command(format="custom", compress=True)

        (cmd,) = captured
        assert not any(a.startswith("--compress") for a in cmd)
