from pathlib import Path
from typing import Any

import yaml
from allauth.headless.constants import Flow
from allauth.headless.spec.internal.schema import get_schema
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from django.urls import NoReverseMatch, clear_url_caches


class Command(BaseCommand):
    help = (
        "Write the OpenAPI document of allauth's headless API, adapted to "
        "this project's settings, adapter and signup form"
    )

    # The checks load the URLconf, and allauth decides whether to mount its
    # specification routes when it first imports its URLs: with the
    # setting flipped below, that import has to be the first one.
    requires_system_checks = []

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--file",
            default="allauth-schema.yml",
            help="Where to write the document (default: %(default)s)",
        )

    @staticmethod
    def declare_every_flow(document: dict[str, Any]) -> None:
        """List every flow allauth can answer with in ``Flow.id``.

        The specification's enum is written by hand and lags allauth's own
        ``Flow``: it omits ``mfa_login_webauthn`` (passkey login),
        ``mfa_signup_webauthn``, ``mfa_trust`` and
        ``password_reset_by_code``, which the responses do carry. Read from
        the constants, so it is a no-op once allauth lists them itself.
        """
        schema = document["components"]["schemas"]["Flow"]["properties"]["id"]
        schema["enum"] = sorted(
            {*schema["enum"], *(flow.value for flow in Flow)}
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # ``get_schema()`` roots its paths at the URL of its own
        # ``openapi.yaml`` route, which allauth mounts only when
        # ``HEADLESS_SERVE_SPECIFICATION`` is on. It is on here, for this
        # one generation, and never in a running deployment.
        with override_settings(HEADLESS_SERVE_SPECIFICATION=True):
            clear_url_caches()
            try:
                document = get_schema()
            except NoReverseMatch as error:
                raise CommandError(
                    "allauth's specification route is not mounted: the "
                    "URLconf was imported before the setting was on."
                ) from error
        clear_url_caches()
        self.declare_every_flow(document)

        Path(options["file"]).write_text(
            yaml.safe_dump(
                document,
                sort_keys=False,
                allow_unicode=True,
                width=80,
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.stdout.write(
            self.style.SUCCESS(f"Wrote {options['file']}"),
        )
