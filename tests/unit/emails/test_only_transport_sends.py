"""Nothing sends mail but ``emails/transport.py``.

Every email is an email type sent through ``emails.send.send_email``, which
decides whether to send (gates, the store's switch, recipients, opt-out),
renders, builds the headers and hands the message to the transport. A
module that builds its own message or calls ``send_mail`` or ``mail_admins``
would skip all of that: no switch, no opt-out, no list headers, no golden
file. This reads every module's syntax tree, so a new send site fails here
before it reaches a customer.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

#: Django's ways to put a message on the wire (``django.core.mail``).
SENDERS = frozenset(
    {
        "EmailMessage",
        "EmailMultiAlternatives",
        "send_mail",
        "send_mass_mail",
        "mail_admins",
        "mail_managers",
    }
)

ROOT = Path(__file__).resolve().parents[3]
TRANSPORT = ROOT / "emails" / "transport.py"
#: Not the project's modules: dependencies, and the tests, which read the
#: outbox and may build a message to assert on. Every virtualenv in the
#: checkout is skipped too, at any depth and whatever its name
#: (``UV_PROJECT_ENVIRONMENT``): a directory holding a ``pyvenv.cfg``.
SKIPPED_DIRECTORIES = frozenset({".git", "node_modules", "tests", "tests_mt"})


def project_modules() -> Iterator[Path]:
    virtualenvs = [config.parent for config in ROOT.rglob("pyvenv.cfg")]
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if SKIPPED_DIRECTORIES.intersection(relative.parts):
            continue
        if any(path.is_relative_to(venv) for venv in virtualenvs):
            continue
        yield path


def senders_in(source: str) -> list[tuple[int, str]]:
    """Where *source* imports or calls one of :data:`SENDERS`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            found.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if alias.name in SENDERS
            )
        elif isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if name in SENDERS:
                found.append((node.lineno, name))
    return found


def test_no_module_outside_the_transport_sends_mail_itself():
    offenders = [
        f"{path.relative_to(ROOT)}:{line} {name}"
        for path in project_modules()
        if path != TRANSPORT
        for line, name in senders_in(path.read_text(encoding="utf-8"))
    ]

    assert not offenders, (
        "send through emails.send.send_email, declaring an email type in "
        f"<app>/emails.py: {offenders}"
    )


def test_the_transport_is_where_the_message_is_built():
    """The guard looks for the right thing: the transport does build one."""
    assert senders_in(TRANSPORT.read_text(encoding="utf-8"))


def test_the_guard_sees_every_way_in():
    source = (
        "from django.core.mail import send_mail, mail_admins as alert\n"
        "import django.core.mail as mail\n"
        "mail.send_mail('s', 'b', 'f', ['t'])\n"
        "EmailMultiAlternatives(subject='s')\n"
    )

    assert {name for _line, name in senders_in(source)} == {
        "send_mail",
        "mail_admins",
        "EmailMultiAlternatives",
    }
