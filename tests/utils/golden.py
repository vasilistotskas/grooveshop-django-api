"""Golden files for outbound mail: what a customer or a store reads.

A golden test sends a mail through its real code path and compares what
reached ``mail.outbox`` with files recorded earlier, so a change that
rewords, re-routes or re-styles an email is a visible diff in review and
never a surprise in an inbox. Per mail and language, three files in
``<directory>/<name>/``:

* ``<language>.json``: subject, recipients, From, Reply-To, the extra
  headers and the attachments' names and types (never their bytes);
* ``<language>.html`` and ``<language>.txt``: the two bodies.

A deliberate change to a mail re-records its files in the same commit
(``GOLDEN_UPDATE=1 uv run pytest <test>``), where the reviewer sees the
difference. Nothing else may write them.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

from django.core.mail import EmailMultiAlternatives

UPDATE_ENV = "GOLDEN_UPDATE"

#: The only value in a body that follows the clock rather than the
#: fixture: the footer's copyright year (``{% now "Y" %}``).
_COPYRIGHT_YEAR = re.compile(r"(&copy;|©)(\s*)\d{4}")


def stable(body: str) -> str:
    return _COPYRIGHT_YEAR.sub(r"\1\2{YEAR}", body.replace("\r\n", "\n"))


def snapshot(message: EmailMultiAlternatives) -> dict[str, str]:
    """The files a sent message is compared with, by file suffix."""
    [(html, mimetype)] = message.alternatives
    assert mimetype == "text/html"
    meta = {
        "subject": message.subject,
        "to": message.to,
        "from": message.from_email,
        "reply_to": message.reply_to,
        "headers": message.extra_headers,
        "attachments": [
            [attachment[0], attachment[2]] for attachment in message.attachments
        ],
    }
    return {
        "json": json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        "html": stable(html),
        "txt": stable(message.body),
    }


def assert_golden(
    directory: Path, name: str, language: str, files: dict[str, str]
) -> None:
    folder = directory / name
    update = os.environ.get(UPDATE_ENV) == "1"
    for suffix, actual in files.items():
        path = folder / f"{language}.{suffix}"
        if update:
            folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(actual.encode())
            continue
        assert path.exists(), (
            f"{path} is not recorded: run it with {UPDATE_ENV}=1 once the "
            "mail is what it should be"
        )
        expected = path.read_bytes().decode().replace("\r\n", "\n")
        if expected != actual:
            diff = "".join(
                difflib.unified_diff(
                    expected.splitlines(keepends=True),
                    actual.splitlines(keepends=True),
                    fromfile=f"recorded {path.name}",
                    tofile="sent",
                )
            )
            raise AssertionError(
                f"{name} ({language}) {suffix} differs from {path}:\n{diff}"
            )
