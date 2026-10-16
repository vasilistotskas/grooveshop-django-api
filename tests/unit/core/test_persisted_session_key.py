"""``persisted_session_key`` answers only with sessions that exist.

Before a session is loaded its ``session_key`` is the raw cookie, so the
analytics stored whatever a client sent (2026-10-13: a 227-character
value failed every ``save_search_query`` insert).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.sessions.backends.db import SessionStore

from core.sessions import persisted_session_key

pytestmark = pytest.mark.django_db


def test_a_stored_session_answers_with_its_key():
    stored = SessionStore()
    stored["seen"] = True
    stored.save()

    request = SimpleNamespace(
        session=SessionStore(session_key=stored.session_key)
    )

    assert persisted_session_key(request) == stored.session_key


@pytest.mark.parametrize("cookie", ["a" * 227, "not-a-session-anyone-has"])
def test_a_cookie_with_no_stored_session_answers_empty(cookie):
    request = SimpleNamespace(session=SessionStore(session_key=cookie))

    assert persisted_session_key(request) == ""


def test_no_session_answers_empty():
    assert persisted_session_key(SimpleNamespace()) == ""
    assert persisted_session_key(SimpleNamespace(session=SessionStore())) == ""
