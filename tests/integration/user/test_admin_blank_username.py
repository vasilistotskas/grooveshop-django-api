"""Adding a user account from the admin without inventing a handle.

The site owner could not add a blog author. ``BlogAuthor.user`` is a
required OneToOne to ``UserAccount``, so adding an author means adding a
user first — and the admin's ``username`` field, which is
``blank=True, null=True`` and always has been, carried the help text
"Required. 30 characters or fewer...".

So he read "Required", typed the author's actual name
("Κωνσταντίνος Βάσκος") into what is a machine handle, and
``ExtendedUnicodeUsernameValidator`` rejected it — correctly, on the
SPACE, not on the Greek letters (``\\w`` is Unicode-aware).

The label was the bug. These tests pin the behaviour that makes the
label honest: blank works, and a handle appears without the operator
naming one.
"""

from __future__ import annotations

from unittest import mock

from django.test import TestCase

from core.generators import UserNameGenerator
from user.forms import UserAccountCreationForm
from user.models import UserAccount
from user.validators import ExtendedUnicodeUsernameValidator


class BlankUsernameGetsAHandleTests(TestCase):
    def _form(self, **overrides):
        data = {
            "email": "konstantinosvaskos@hotmail.com",
            "username": "",
            "password1": "aVeryLongPassphrase42",
            "password2": "aVeryLongPassphrase42",
        }
        data.update(overrides)
        return UserAccountCreationForm(data=data)

    def test_blank_username_is_accepted(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors.as_data())

    def test_blank_username_is_filled_not_stored_null(self):
        # NULL is legal on the column, but it leaves get_short_name()
        # returning None, which some admin surfaces render literally.
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors.as_data())
        user = form.save()

        self.assertTrue(user.username)
        self.assertIsNotNone(user.username)

    def test_the_generated_handle_passes_the_validator(self):
        # A generated value that the field's own validator would reject
        # would just move the failure to the next save.
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors.as_data())
        user = form.save()

        ExtendedUnicodeUsernameValidator()(user.username)

    def test_an_explicit_handle_is_left_alone(self):
        form = self._form(username="k.vaskos")
        self.assertTrue(form.is_valid(), form.errors.as_data())
        user = form.save()

        self.assertEqual(user.username, "k.vaskos")

    def test_whitespace_only_counts_as_blank(self):
        form = self._form(username="   ")
        self.assertTrue(form.is_valid(), form.errors.as_data())
        user = form.save()

        self.assertNotEqual(user.username.strip(), "")


class BlankSubmissionDoesNotCrashTests(TestCase):
    """The 500 that made "leave it blank" impossible.

    Django's ``UsernameField.to_python`` does ``len(value)`` with no
    None guard. Its own ``User.username`` can never be null, so that is
    safe upstream — but this column IS ``null=True``, so an empty
    submission raised ``TypeError: object of type 'NoneType' has no
    len()`` and the admin add view 500'd.

    Combined with the "Required." help text, that left no working path:
    obey the label and hit the no-spaces validator, ignore it and hit a
    500.
    """

    def test_empty_username_does_not_raise(self):
        form = UserAccountCreationForm(
            data={
                "email": "blank@example.com",
                "username": "",
                "password1": "aVeryLongPassphrase42",
                "password2": "aVeryLongPassphrase42",
            }
        )

        # The assertion is that this does not raise at all — is_valid()
        # runs to_python on every field.
        self.assertTrue(form.is_valid(), form.errors.as_data())

    def test_missing_username_key_does_not_raise(self):
        # A field omitted from the POST entirely, not merely empty —
        # which is what a browser sends for a disabled/hidden input.
        form = UserAccountCreationForm(
            data={
                "email": "omitted@example.com",
                "password1": "aVeryLongPassphrase42",
                "password2": "aVeryLongPassphrase42",
            }
        )

        self.assertTrue(form.is_valid(), form.errors.as_data())
        self.assertTrue(form.save().username)


class DisplayNameStillBelongsInNameFieldsTests(TestCase):
    def test_a_greek_display_name_is_still_rejected_as_a_handle(self):
        # This must KEEP failing. The fix is not "allow spaces in
        # usernames" — a handle with a space is not URL-safe and would
        # collide with display names. The fix is that nobody has to
        # type one.
        form = UserAccountCreationForm(
            data={
                "email": "konstantinosvaskos@hotmail.com",
                "username": "Κωνσταντίνος Βάσκος",
                "password1": "aVeryLongPassphrase42",
                "password2": "aVeryLongPassphrase42",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_greek_letters_alone_are_fine(self):
        # Proves the rejection above is about the SPACE, not the
        # alphabet — worth pinning so nobody "fixes" the validator's
        # regex to be ASCII-only and breaks Greek handles.
        ExtendedUnicodeUsernameValidator()("Κωνσταντίνος")

    def test_the_byline_reads_the_name_fields_not_the_handle(self):
        # Why the handle never mattered for a blog author: full_name —
        # which BlogAuthor.full_name and every byline read — is built
        # from first/last name.
        user = UserAccount.objects.create_user(
            email="author@example.com",
            password="aVeryLongPassphrase42",
            first_name="Κωνσταντίνος",
            last_name="Βάσκος",
        )

        self.assertEqual(user.full_name, "Κωνσταντίνος Βάσκος")


class GeneratedHandleDoesNotFailThePasswordTests(TestCase):
    """A handle the operator never chose cannot make their password invalid.

    ``clean_username`` invents the handle during cleaning, so it is on
    the instance by the time Django validates the password against the
    user's attributes. ``UserNameGenerator`` builds it from an
    adjective, a noun and a hash of the email, so it occasionally lands
    within ``UserAttributeSimilarityValidator``'s 0.7 threshold — and
    the operator is told the password is "too similar to the username"
    for a value they never typed and cannot see.

    That is also why CI failed intermittently here: reproduced 1 run in
    8 against the fixed password below.
    """

    def _form(self, **overrides):
        data = {
            "email": "konstantinosvaskos@hotmail.com",
            "username": "",
            "password1": "aVeryLongPassphrase42",
            "password2": "aVeryLongPassphrase42",
        }
        data.update(overrides)
        return UserAccountCreationForm(data=data)

    def test_a_handle_resembling_the_password_is_not_held_against_it(self):
        # The worst case the generator can produce: the handle IS the
        # password. Random adjective/noun pairs only approach this, so
        # pinning it here is what makes the flake deterministic.
        with mock.patch.object(
            UserNameGenerator,
            "generate_username",
            return_value="aVeryLongPassphrase42",
        ):
            form = self._form()

            self.assertTrue(form.is_valid(), form.errors.as_data())

    def test_a_handle_the_operator_typed_still_counts(self):
        # Only a GENERATED handle is excused. One the operator chose is
        # a real signal, and they can change it.
        form = self._form(
            username="aVeryLongPassphrase42",
            password1="aVeryLongPassphrase42",
            password2="aVeryLongPassphrase42",
        )

        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)

    def test_the_email_is_still_checked(self):
        # The user chose the email, so a password resembling it is a
        # real weakness — excusing the handle must not excuse this.
        form = self._form(
            password1="konstantinosvaskos",
            password2="konstantinosvaskos",
        )

        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)
