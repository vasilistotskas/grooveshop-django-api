from os import listdir
from os import path
from os import remove

from django.conf import settings
from django.core.management.base import BaseCommand

from core.logging import LogInfo


class Command(BaseCommand):
    help = "Delete all log files"

    def handle(self, *args, **kwargs):
        try:
            logs_path = path.join(settings.BASE_DIR, "logs")
            files = listdir(logs_path)

            for file in files:
                file_path = path.join(logs_path, file)
                try:
                    self.stdout.write(f"Deleting file: {file_path}")
                    remove(file_path)
                except Exception as e:
                    warning_message = f"Could not delete file {file_path}: {str(e)}"
                    LogInfo.warning(warning_message)
                    self.stdout.write(self.style.WARNING(warning_message))

            message = "Attempted to remove all log files."
            LogInfo.info(message)
            self.stdout.write(self.style.SUCCESS(message))
        except Exception as e:
            error_message = f"Error while deleting log files: {str(e)}"
            LogInfo.error(error_message)
            self.stdout.write(self.style.ERROR(error_message))
