"""``makemessages`` that leaves the catalogues in canonical form.

Django's command appends new messages to the end of each ``.po`` and
rewrites every ``#:`` line number, so each PR that adds a message conflicts
with every other. This one writes no locations and sorts the result (see
``core.utils.po_catalogue``). The name shadows Django's on purpose: it is
the command everyone already runs.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.commands.makemessages import (
    Command as MakeMessagesCommand,
)

from core.utils.po_catalogue import normalise


class Command(MakeMessagesCommand):
    def handle(self, *args, **options):
        options["no_location"] = True
        super().handle(*args, **options)
        vendored = Path(settings.UNFOLD_LOCALE_PATH).resolve()
        for locale_path in map(Path, self.locale_paths):
            if locale_path.resolve() == vendored:
                continue  # ``makemessages_unfold`` owns that catalogue
            for catalogue in locale_path.glob(
                f"*/LC_MESSAGES/{self.domain}.po"
            ):
                normalise(catalogue)
