"""Extract django-unfold's own strings into the project's catalogue for them.

Unfold ships no translations, and its maintainers leave them to each
project (github.com/unfoldadmin/django-unfold/issues/596), so its own
labels ("Filters", "Apply Filters", the sidebar search) read English
under the Greek admin. ``makemessages`` only walks the project tree; this
walks the installed Unfold package instead and writes to
``settings.UNFOLD_LOCALE_PATH``, which ``LOCALE_PATHS`` loads after the
project's own catalogue. Run it after every Unfold upgrade, then
translate what it added. Use gettext 0.23 or later: older xgettext skips
calls inside f-strings, and with ``no_obsolete`` would drop the strings
they hold.

Strings Django's own catalogues already translate are left out, so
Django's wording stays the one the admin shows for them.
"""

from __future__ import annotations

from pathlib import Path

import django
import polib
import unfold
from django.conf import settings
from django.core.management.commands import makemessages

#: Django's catalogues whose translations win over ours.
DJANGO_CATALOGUES = ("conf", "contrib/admin", "contrib/auth")


class Command(makemessages.Command):
    help = "Extract django-unfold's strings into settings.UNFOLD_LOCALE_PATH."

    def handle(self, *args, **options) -> None:
        # Locations would be paths into one machine's virtualenv, and a
        # string Unfold dropped has nothing left to translate.
        options.update(no_location=True, no_obsolete=True)
        super().handle(*args, **options)
        for locale in options["locale"]:
            self._drop_strings_django_translates(locale)

    def find_files(self, root):
        package = Path(unfold.__file__).parent
        target = str(Path(settings.UNFOLD_LOCALE_PATH).resolve())
        # ``build_potfiles`` only collects the .pot files of these paths.
        if target not in self.locale_paths:
            self.locale_paths.append(target)
        return [
            self.translatable_file_class(found.dirpath, found.file, target)
            for found in super().find_files(str(package))
        ]

    def _drop_strings_django_translates(self, locale: str) -> None:
        catalogue_path = (
            Path(settings.UNFOLD_LOCALE_PATH)
            / locale
            / "LC_MESSAGES"
            / f"{self.domain}.po"
        )
        if not catalogue_path.exists():
            return
        translated = set()
        django_root = Path(django.__file__).parent
        for name in DJANGO_CATALOGUES:
            source = (
                django_root
                / name
                / "locale"
                / locale
                / "LC_MESSAGES"
                / "django.po"
            )
            if source.exists():
                translated |= {
                    (entry.msgctxt, entry.msgid)
                    for entry in polib.pofile(str(source))
                    if entry.translated()
                }
        catalogue = polib.pofile(str(catalogue_path))
        for entry in [
            entry
            for entry in catalogue
            if (entry.msgctxt, entry.msgid) in translated
        ]:
            catalogue.remove(entry)
        catalogue.save()
