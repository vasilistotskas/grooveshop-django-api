"""A user renders as the person, not the generated handle.

Usernames here are generated (``{Adjective}{Noun}#{hash}``, see
``core/generators.py``), so ``str(user)`` returning one showed staff
``Paok1441`` on the Blog Author admin page for an account plainly
holding "Webside Admin". Every admin surface that renders a user
through a relation — a ``user`` form field, autocomplete results, FK
columns, log entries — goes through this method, and the columns that
mattered had each worked around it locally.
"""

from __future__ import annotations

import pytest

from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def _user(**kwargs):
    return UserAccountFactory(num_addresses=0, **kwargs)


def test_a_named_account_reads_as_the_person():
    user = _user(first_name="Webside", last_name="Admin")

    assert str(user) == "Webside Admin"


def test_a_first_name_alone_is_enough():
    user = _user(first_name="Μαρία", last_name="")

    assert str(user) == "Μαρία"


def test_an_unnamed_account_falls_back_to_the_username():
    """Still identifying, and never blank — a dropdown entry with no
    label cannot be picked."""
    user = _user(first_name="", last_name="", username="QuietOtter#4417")

    assert str(user) == "QuietOtter#4417"


def test_email_is_the_last_resort():
    user = _user(first_name="", last_name="")
    user.username = ""

    assert str(user) == user.email
