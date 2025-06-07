import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a PostgreSQL database backup"

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        try:
            self.stdout.write("Closing Django database connections...")
            connections.close_all()

            output_dir = Path(settings.BASE_DIR) / options["output_dir"]
            output_dir.mkdir(parents=True, exist_ok=True)

            if options["filename"]:
                filename = options["filename"]
            else:
                timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
                filename = f"backup_{timestamp}"

            if options["format"] == "custom":
                extension = ".dump"
            elif options["compress"]:
                extension = ".sql.gz"
            else:
                extension = ".sql"

            backup_path = output_dir / f"{filename}{extension}"

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

            if options["format"] in ["custom", "tar"]:
                pg_dump_cmd.append(f"--file={backup_path}")

            env = os.environ.copy()
            env["PGPASSWORD"] = db_config["PASSWORD"]

            self.stdout.write(f"Creating database backup: {backup_path}")

            if options["compress"] and options["format"] == "plain":
                with open(backup_path, "wb") as f:
                    pg_dump = subprocess.Popen(
                        pg_dump_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                    )
                    gzip_process = subprocess.Popen(
                        ["gzip"],
                        stdin=pg_dump.stdout,
                        stdout=f,
                        stderr=subprocess.PIPE,
                    )
                    pg_dump.stdout.close()

                    gzip_output, gzip_error = gzip_process.communicate()
                    pg_dump_output, pg_dump_error = pg_dump.communicate()

                    if pg_dump.returncode != 0:
                        error_msg = pg_dump_error.decode(
                            "utf-8", errors="replace"
                        )
                        raise CommandError(f"pg_dump failed: {error_msg}")
                    if gzip_process.returncode != 0:
                        error_msg = gzip_error.decode("utf-8", errors="replace")
                        raise CommandError(f"gzip failed: {error_msg}")

            elif options["format"] == "plain" and not options["compress"]:
                result = subprocess.run(
                    pg_dump_cmd,
                    env=env,
                    capture_output=True,
                    timeout=3600,
                    check=False,
                )

                if result.returncode != 0:
                    error_msg = result.stderr.decode("utf-8", errors="replace")
                    raise CommandError(f"pg_dump failed: {error_msg}")

                try:
                    output_text = result.stdout.decode("utf-8")
                except UnicodeDecodeError:
                    output_text = result.stdout.decode("latin-1")

                with open(backup_path, "w", encoding="utf-8") as f:
                    f.write(output_text)

            else:
                result = subprocess.run(
                    pg_dump_cmd,
                    env=env,
                    capture_output=True,
                    timeout=3600,
                    check=False,
                )

                if result.returncode != 0:
                    error_msg = result.stderr.decode("utf-8", errors="replace")
                    raise CommandError(f"pg_dump failed: {error_msg}")

            if not backup_path.exists() or backup_path.stat().st_size == 0:
                raise CommandError("Backup file was not created or is empty")

            file_size = backup_path.stat().st_size
            self.stdout.write(
                self.style.SUCCESS(
                    f"Database backup completed successfully!\n"
                    f"File: {backup_path}\n"
                    f"Size: {file_size:,} bytes"
                )
            )

        except subprocess.TimeoutExpired as e:
            raise CommandError("Database backup timed out") from e
        except Exception as e:
            raise CommandError(f"Backup failed: {e!s}") from e
        finally:
            connections.close_all()
