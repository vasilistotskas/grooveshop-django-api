"""The one canonical form of a committed ``.po`` catalogue.

Every PR that adds a message used to append it to the end of the file, so
any two open PRs conflicted on the same lines. In canonical form the
messages are sorted by ``msgid``, carry no ``#:`` source locations (their
line numbers change with every edit of the code), and the header's
``POT-Creation-Date`` is pinned. Two PRs that add different messages then
touch different places in the file, and git merges them.

``manage.py makemessages`` (``core/management/commands/makemessages.py``)
writes this form, and ``tests/unit/test_locale_catalogue.py`` fails on a
catalogue that is not in it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

POT_CREATION_DATE = re.compile(r'^"POT-Creation-Date: [^"]*\\n"$', re.MULTILINE)
PINNED_POT_CREATION_DATE = r'"POT-Creation-Date: YEAR-MO-DA HO:MI+ZONE\n"'


def canonical_text(catalogue: Path) -> str:
    """The catalogue's content in canonical form (needs gettext's ``msgcat``)."""
    sorted_messages = subprocess.run(
        [
            "msgcat",
            "--sort-output",
            "--no-location",
            "--no-wrap",
            str(catalogue),
        ],
        check=True,
        capture_output=True,
        text=True,  # universal newlines: msgcat.exe writes CRLF on Windows
        encoding="utf-8",
    ).stdout
    return POT_CREATION_DATE.sub(
        lambda _: PINNED_POT_CREATION_DATE, sorted_messages, count=1
    )


def normalise(catalogue: Path) -> None:
    """Rewrite the catalogue in canonical form, LF line endings."""
    text = canonical_text(catalogue)  # read in full before the file is reopened
    with catalogue.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
