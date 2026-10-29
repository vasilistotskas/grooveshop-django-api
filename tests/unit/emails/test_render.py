"""Rendering one email, and keeping a header safe."""

from __future__ import annotations

from unittest import mock

import pytest

from emails.recipients import Recipient
from emails.render import (
    SUBJECT_MAX_LENGTH,
    clean_header_text,
    format_subject,
    render,
)
from emails.types import RenderEnv
from tests.utils.emails import probe, probe_object

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("probe_templates"),
]


class TestCleanHeaderText:
    def test_a_line_break_cannot_start_another_header(self):
        cleaned = clean_header_text("Order 7\r\nBcc: attacker@evil.test")

        assert cleaned == "Order 7 Bcc: attacker@evil.test"
        assert "\n" not in cleaned
        assert "\r" not in cleaned

    @pytest.mark.parametrize(
        "separator", ["\n", "\r", "\r\n", "\t", " ", "\x85", "\x0b", "\x1c"]
    )
    def test_every_kind_of_break_becomes_one_space(self, separator):
        assert clean_header_text(f"a{separator}b") == "a b"

    def test_whitespace_runs_collapse(self):
        assert clean_header_text("  a \t\n  b  ") == "a b"

    @pytest.mark.parametrize(
        "control", ["\x00", "\x07", "\x1b", "\x7f", "\x9f"]
    )
    def test_control_characters_are_dropped(self, control):
        assert clean_header_text(f"a{control}b") == "ab"

    def test_greek_and_symbols_are_kept(self):
        assert clean_header_text("Παραγγελία €5 — ✓") == "Παραγγελία €5 — ✓"

    def test_the_length_is_capped(self):
        cleaned = clean_header_text("x" * (SUBJECT_MAX_LENGTH + 50))

        assert cleaned == "x" * SUBJECT_MAX_LENGTH

    def test_the_cap_leaves_no_trailing_space(self):
        text = "x" * (SUBJECT_MAX_LENGTH - 1) + " tail"

        assert clean_header_text(text) == "x" * (SUBJECT_MAX_LENGTH - 1)


def _env(language: str) -> RenderEnv:
    return RenderEnv(
        language=language, recipient=Recipient("a@example.test", language)
    )


class TestRender:
    @pytest.mark.parametrize(
        ("language", "subject"),
        [
            ("en", "Your Order #7 Has Shipped"),
            ("el", "Η παραγγελία σας #7 έχει αποσταλεί"),
        ],
    )
    def test_the_subject_is_translated_into_the_recipients_language(
        self, language, subject
    ):
        rendered = render(probe(), probe_object(), _env(language))

        assert rendered.subject == subject
        assert rendered.language == language

    def test_both_bodies_print_the_builders_context(self):
        rendered = render(probe(), probe_object(marker="CONTEXT"), _env("en"))

        assert "CONTEXT" in rendered.text
        assert "<p>CONTEXT</p>" in rendered.html

    def test_the_shared_email_context_is_there(self, settings):
        settings.SITE_NAME = "Shared Site Name"

        rendered = render(probe(), probe_object(), _env("en"))

        assert "Shared Site Name" in rendered.text

    def test_a_subject_parameter_cannot_break_the_header(self):
        email_type = probe(subject="{marker}")
        obj = probe_object(marker="Hi\r\nBcc: attacker@evil.test")

        rendered = render(email_type, obj, _env("en"))

        assert rendered.subject == "Hi Bcc: attacker@evil.test"

    def test_a_subject_naming_a_value_the_builder_lacks_fails(self):
        email_type = probe(subject="{missing}")

        with pytest.raises(KeyError):
            render(email_type, probe_object(), _env("en"))


class TestFormatSubject:
    def test_named_fields_are_stringified_and_braces_unescaped(self):
        assert format_subject("#{n} {{x}}", {"n": 7}) == "#7 {x}"

    def test_an_object_is_never_traversed(self):
        with pytest.raises(ValueError):
            format_subject("{order.password}", {"order": object()})


class TestBadTranslation:
    @pytest.mark.parametrize(
        "msgstr",
        [
            "{marker.__class__}",
            "{marker[0]}",
            "{marker!r}",
            "{other}",
            "{unclosed",
        ],
    )
    def test_a_bad_msgstr_renders_the_msgid_and_warns(self, msgstr, caplog):
        email_type = probe(subject="Sale {marker}")

        with (
            mock.patch("emails.render.gettext", return_value=msgstr),
            caplog.at_level("WARNING", logger="emails.render"),
        ):
            rendered = render(email_type, probe_object(marker="X"), _env("en"))

        assert rendered.subject == "Sale X"
        assert "ignored" in caplog.text

    def test_a_good_msgstr_is_used(self):
        email_type = probe(subject="Sale {marker}")

        with mock.patch(
            "emails.render.gettext", return_value="Εκπτωση {marker}"
        ):
            rendered = render(email_type, probe_object(marker="X"), _env("el"))

        assert rendered.subject == "Εκπτωση X"

    def test_a_line_break_in_a_value_still_gives_one_line(self):
        email_type = probe(subject="Sale {marker}")

        rendered = render(
            email_type,
            probe_object(marker="X\r\nBcc: a@evil.test"),
            _env("en"),
        )

        assert rendered.subject == "Sale X Bcc: a@evil.test"
