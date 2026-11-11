import os
import subprocess
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

# Ceiling for one pg_dump invocation. It is a ceiling, not an estimate:
# the point is that a dump which stops making progress fails the task
# instead of pinning a worker, which is what the nightly backup needs.
PG_DUMP_TIMEOUT_SECONDS = 3600


class Command(BaseCommand):
    help = "Create a PostgreSQL database backup"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--output-dir",
            type=str,
            default="backups",
            help="Directory to store backup files (relative to project root)",
        )
        parser.add_argument(
            "--filename",
            type=str,
            help="Custom filename for the backup (without extension)",
        )
        parser.add_argument(
            "--compress",
            action="store_true",
            help="Compress the backup with gzip",
        )
        parser.add_argument(
            "--format",
            choices=["custom", "plain", "tar"],
            default="custom",
            help="Output format for pg_dump",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        backup_path: Path | None = None
        try:
            self.stdout.write("Closing Django database connections...")
            connections.close_all()

            backup_path = self._setup_backup_path(options)
            pg_dump_cmd = self._build_pg_dump_command(options, backup_path)

            self.stdout.write(f"Creating database backup: {backup_path}")
            self._execute_backup(pg_dump_cmd)
            self._validate_backup(backup_path)

            self._report_success(backup_path)

        except subprocess.TimeoutExpired as e:
            self._discard_partial_backup(backup_path)
            raise CommandError("Database backup timed out") from e
        except Exception as e:
            self._discard_partial_backup(backup_path)
            raise CommandError(f"Backup failed: {e!s}") from e
        finally:
            connections.close_all()

    @staticmethod
    def _discard_partial_backup(backup_path: Path | None) -> None:
        # pg_dump ``--file`` creates the output before it connects, so a
        # dump that aborts (server version mismatch, auth, timeout) leaves
        # a zero-byte or truncated file behind. Left in place it is
        # indistinguishable from a backup in a directory listing and it
        # displaces real dumps inside cleanup_old_backups' retention
        # window — prod held eight empty nightly files and no valid
        # scheduled backup on 2026-09-02.
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)

    def _setup_backup_path(self, options: dict[str, Any]) -> Path:
        output_dir = Path(settings.BASE_DIR) / options["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)

        if options["filename"]:
            filename = options["filename"]
        else:
            timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backup_{timestamp}"

        extension = self._get_file_extension(options)
        return output_dir / f"{filename}{extension}"

    def _get_file_extension(self, options: dict[str, Any]) -> str:
        # `tar` used to fall through to the plain branch and be written
        # as `.sql` — a tar archive named as SQL, which nothing but
        # pg_restore could make sense of. `--compress` only ever reaches
        # the file name for plain output; the archive formats carry
        # their compression inside the container.
        if options["format"] == "custom":
            return ".dump"
        if options["format"] == "tar":
            return ".tar"
        return ".sql.gz" if options["compress"] else ".sql"

    def _build_pg_dump_command(
        self, options: dict[str, Any], backup_path: Path
    ) -> list[str]:
        db_config = settings.DATABASES["default"]

        pg_dump_cmd = [
            "pg_dump",
            f"--host={db_config['HOST']}",
            f"--port={db_config['PORT']}",
            f"--username={db_config['USER']}",
            f"--dbname={db_config['NAME']}",
            "--verbose",
            "--clean",
            "--no-owner",
            "--no-privileges",
            "--serializable-deferrable",
        ]

        if options["format"] != "plain":
            pg_dump_cmd.append(f"--format={options['format']}")

        # pg_dump writes the file itself for EVERY format, and compresses
        # it itself when asked. What this replaced piped stdout into
        # `gzip` for the plain+compress case, and that pipeline
        # deadlocked: `--verbose` writes steadily to a stderr PIPE that
        # nothing drains until after `gzip.communicate()` returns, and
        # `gzip.communicate()` cannot return until pg_dump closes stdout.
        # Once the stderr buffer fills, pg_dump blocks, gzip waits for
        # input, and neither ever moves. Reproduced with the same process
        # shape: 4 KiB of stderr completes, 8 KiB hangs. That path also
        # carried no timeout, so it hung forever rather than raising.
        #
        # The plain-uncompressed case read the whole dump into memory and
        # decoded it, falling back to latin-1 on failure — which would
        # silently corrupt the SQL of any UTF-8 database.
        #
        # `--compress=METHOD` with plain output is verified against the
        # deployed engine (pg_dump 18.6: `-Fp -Z gzip --file=x.sql.gz`
        # produces a file `gzip -t` accepts and `gzip -dc` reads back as
        # SQL). The client major tracks the server major, see Dockerfile.
        if options["format"] == "plain" and options["compress"]:
            pg_dump_cmd.append("--compress=gzip")

        pg_dump_cmd.append(f"--file={backup_path}")

        return pg_dump_cmd

    def _get_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        db_config = settings.DATABASES["default"]
        env["PGPASSWORD"] = db_config["PASSWORD"]
        return env

    def _execute_backup(self, pg_dump_cmd: list[str]) -> None:
        """One implementation for every format.

        `pg_dump` owns the output file (`--file`), so nothing is piped
        and nothing is buffered in this process. `subprocess.run` drains
        stderr through `communicate()`, which reads both streams
        concurrently — the property the hand-rolled pipeline lacked.
        """
        result = subprocess.run(
            pg_dump_cmd,
            env=self._get_environment(),
            capture_output=True,
            timeout=PG_DUMP_TIMEOUT_SECONDS,
            check=False,
        )

        if result.returncode != 0:
            error_msg = result.stderr.decode("utf-8", errors="replace")
            raise CommandError(f"pg_dump failed: {error_msg}")

    def _validate_backup(self, backup_path: Path) -> None:
        if not backup_path.exists() or backup_path.stat().st_size == 0:
            raise CommandError("Backup file was not created or is empty")

    def _report_success(self, backup_path: Path) -> None:
        file_size = backup_path.stat().st_size
        self.stdout.write(
            self.style.SUCCESS(
                f"Database backup completed successfully!\n"
                f"File: {backup_path}\n"
                f"Size: {file_size:,} bytes"
            )
        )
