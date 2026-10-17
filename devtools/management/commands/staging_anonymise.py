"""Replace every customer identity in the staging database.

    manage.py staging_anonymise

Step 4 of ``grooveshop-infrastructure/scripts/staging-refresh.sh``. The
refresh restores a production dump; this command runs against it before
the database is swapped in, so staging services never read the dump's
personal data. Orders, products, stock and amounts stay real; names,
e-mail addresses, phones, addresses, IPs, sessions, tokens, enquiries and
free text are replaced with deterministic fakes, in the public schema and
every tenant schema, and the shared demo logins and platform staff keep
working. A final pass reads every column of every table and fails the run
if an original value is still there.

It refuses to run unless ``SYSTEM_ENV`` is ``staging``. Exit status is
non-zero on any failure, and the whole rewrite is one transaction, so a
failure leaves the database as restored; the refresh script then drops it.

What the command covers, and why it cannot silently fall behind, is in
``devtools/anonymise/rules.py`` and ``docs/staging-anonymise.md``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from devtools.anonymise import engine


class Command(BaseCommand):
    help = (
        "Anonymise every customer identity in the database. Staging only; "
        "run by scripts/staging-refresh.sh against the restored dump."
    )

    def handle(self, *args: object, **options: object) -> None:
        try:
            report = engine.anonymise_database()
        except (
            engine.NotStaging,
            engine.ResidueFound,
            engine.UnknownSchema,
        ) as error:
            raise CommandError(str(error)) from error
        for line in report.lines():
            self.stdout.write(line)
        self.stdout.write(
            self.style.SUCCESS(
                "staging_anonymise: done, no original value left behind"
            )
        )
