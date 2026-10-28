"""Saving, resetting and restoring a store's texts.

The storefront is faked where ``requests`` leaves; everything else,
including the history rows and the permission, is real.
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.utils import translation

from tenant.models import TenantMembershipRole
from tests.utils.staff import store_staff
from tests.utils.texts import COOKIES, FOOTER
from texts import services, storefront
from texts.factories import TextOverrideFactory
from texts.models import TextOverride
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _english():
    """The admin's default language is Greek; these tests read English."""
    with translation.override("en"):
        yield


TAGLINE = {"namespace": FOOTER, "key": "tagline"}


def _save(user, **values):
    return services.save_texts(user, **TAGLINE, values=values)


def _rows():
    return {
        row.language: row.value
        for row in TextOverride.objects.filter(**TAGLINE)
    }


class TestSave:
    def test_a_new_text_is_stored_and_checked_with_the_storefront(
        self, stub, admin_user
    ):
        result = _save(admin_user, en="Welcome home")

        assert result.ok
        assert result.saved == ("en",)
        assert _rows() == {"en": "Welcome home"}
        assert stub.posts[0]["json"]["schema"] == "texts_store"
        assert stub.posts[0]["json"]["locale"] == "en"

    def test_both_languages_in_one_save(self, stub, admin_user):
        _save(admin_user, el="Καλώς ήρθατε", en="Welcome")

        assert _rows() == {"el": "Καλώς ήρθατε", "en": "Welcome"}

    def test_the_value_is_stripped_and_line_breaks_normalised(
        self, stub, admin_user
    ):
        _save(admin_user, en="  one\r\ntwo \r\n")

        assert _rows() == {"en": "one\ntwo"}

    def test_the_staff_member_is_recorded(self, stub, admin_user):
        _save(admin_user, en="Welcome")

        row = TextOverride.objects.get(**TAGLINE, language="en")
        assert row.updated_by_staff_id == admin_user.pk
        assert row.updated_by_staff.pk == admin_user.pk

    def test_a_second_save_updates_the_same_row(self, stub, admin_user):
        _save(admin_user, en="One")
        _save(admin_user, en="Two")

        assert _rows() == {"en": "Two"}
        assert TextOverride.objects.filter(**TAGLINE).count() == 1

    def test_an_unchanged_value_asks_the_storefront_nothing(
        self, stub, admin_user
    ):
        _save(admin_user, en="Welcome")
        stub.posts.clear()

        result = _save(admin_user, en="Welcome")

        assert result.ok
        assert result.saved == ()
        assert stub.posts == []

    def test_the_default_is_not_stored_and_not_checked(self, stub, admin_user):
        result = _save(admin_user, en="Our shop")

        assert result.ok
        assert _rows() == {}
        assert stub.posts == []

    def test_saving_the_default_removes_an_override(self, stub, admin_user):
        _save(admin_user, en="Welcome")

        result = _save(admin_user, en="Our shop")

        assert result.reset == ("en",)
        assert _rows() == {}

    def test_an_untouched_language_with_no_text_is_skipped(
        self, stub, admin_user
    ):
        result = services.save_texts(
            admin_user,
            namespace=FOOTER,
            key="only_greek",
            values={"el": "Μόνο ελληνικά", "en": ""},
        )

        assert result.ok
        assert TextOverride.objects.count() == 0

    def test_an_unknown_language_is_refused(self, stub, admin_user):
        result = _save(admin_user, de="Willkommen")

        assert result.errors["de"].code == "unsupported_language"
        assert _rows() == {}

    def test_all_or_nothing(self, stub, admin_user):
        stub.verdict = lambda ns, key, locale, value: (
            ("syntax_error", "bad") if locale == "el" else None
        )

        result = _save(admin_user, el="{", en="Fine")

        assert set(result.errors) == {"el"}
        assert _rows() == {}


class TestRefusals:
    """Each code the storefront can answer, as a stable Django error."""

    @pytest.mark.parametrize(
        ("code", "fragment"),
        [
            ("unknown_key", "no longer exists"),
            ("locked_key", "managed by the platform"),
            ("syntax_error", "not valid"),
            ("placeholder_mismatch", "same placeholders"),
        ],
    )
    def test_a_storefront_rejection_becomes_a_coded_validation_error(
        self, stub, admin_user, code, fragment
    ):
        stub.verdict = lambda *a: (code, "the detail")

        result = _save(admin_user, en="Welcome")

        error = result.errors["en"]
        assert isinstance(error, ValidationError)
        assert error.code == code
        assert fragment in error.messages[0]
        assert error.params == {"detail": "the detail"}
        assert _rows() == {}

    def test_a_blank_value_is_refused(self, stub, admin_user):
        _save(admin_user, en="Welcome")

        result = _save(admin_user, en="   ")

        assert result.errors["en"].code == "value_blank"
        assert _rows() == {"en": "Welcome"}

    def test_a_value_over_the_cap_is_refused_without_asking(
        self, stub, admin_user, settings
    ):
        settings.TEXTS_MAX_VALUE_LENGTH = 10

        result = _save(admin_user, en="x" * 11)

        error = result.errors["en"]
        assert error.code == "value_too_long"
        assert "11 of 10" in error.messages[0]
        assert stub.posts == []

    def test_a_value_at_the_cap_is_accepted(self, stub, admin_user, settings):
        settings.TEXTS_MAX_VALUE_LENGTH = 10

        assert _save(admin_user, en="x" * 10).ok

    def test_an_unreachable_storefront_refuses_the_save(self, stub, admin_user):
        _save(admin_user, en="Before")
        stub.down = True
        storefront.clear_catalogue_cache()

        with pytest.raises(ValidationError) as caught:
            _save(admin_user, en="After")

        assert caught.value.code == "storefront_unavailable"
        assert _rows() == {"en": "Before"}

    def test_a_validation_that_cannot_be_asked_refuses_the_save(
        self, stub, admin_user
    ):
        _save(admin_user, en="Before")

        with mock.patch(
            "texts.storefront.requests.post",
            side_effect=requests.Timeout("slow"),
        ):
            result = _save(admin_user, en="After")

        assert result.errors["en"].code == "storefront_unavailable"
        assert _rows() == {"en": "Before"}


class TestPermission:
    def test_a_user_without_the_permission_cannot_save(self, stub, tenant):
        nobody = UserAccountFactory(is_staff=True)

        with pytest.raises(PermissionDenied):
            _save(nobody, en="Welcome")

        assert _rows() == {}
        assert stub.posts == []

    @pytest.mark.parametrize(
        "role",
        [
            TenantMembershipRole.STAFF,
            TenantMembershipRole.ADMIN,
            TenantMembershipRole.OWNER,
        ],
    )
    def test_the_store_roles_hold_it(self, tenant, role):
        user = store_staff(tenant, role=role)

        assert user.has_perm(services.CHANGE_PERMISSION)

    def test_a_shopper_does_not(self, tenant):
        shopper = UserAccountFactory()

        assert not shopper.has_perm(services.CHANGE_PERMISSION)

    def test_reset_and_restore_need_it_too(self, stub, tenant):
        nobody = UserAccountFactory(is_staff=True)

        with pytest.raises(PermissionDenied):
            services.reset_text(nobody, **TAGLINE)
        with pytest.raises(PermissionDenied):
            services.restore_text(nobody, history_id=1)


class TestReset:
    def test_every_language_goes_back_to_the_default(self, stub, admin_user):
        _save(admin_user, el="Ένα", en="One")

        assert services.reset_text(admin_user, **TAGLINE) == 2
        assert _rows() == {}

    def test_resetting_what_is_not_changed_does_nothing(self, stub, admin_user):
        assert services.reset_text(admin_user, **TAGLINE) == 0

    def test_another_texts_override_is_untouched(self, stub, admin_user):
        TextOverrideFactory(namespace=FOOTER, key="greeting", language="en")
        _save(admin_user, en="One")

        services.reset_text(admin_user, **TAGLINE)

        assert TextOverride.objects.filter(key="greeting").count() == 1


class TestHistoryAndRestore:
    def _history(self):
        return list(
            TextOverride.history.filter(**TAGLINE, language="en").order_by(
                "history_id"
            )
        )

    def test_every_change_leaves_a_row_naming_who(self, stub, admin_user):
        _save(admin_user, en="One")
        _save(admin_user, en="Two")
        services.reset_text(admin_user, **TAGLINE)

        history = self._history()

        assert [h.history_type for h in history] == ["+", "~", "-"]
        assert [h.value for h in history] == ["One", "Two", "Two"]
        assert {h.history_user_id for h in history} == {admin_user.pk}
        assert history[2].history_change_reason == "Reset to default"

    def test_restoring_an_earlier_value(self, stub, admin_user):
        _save(admin_user, en="One")
        _save(admin_user, en="Two")
        first = self._history()[0]

        language = services.restore_text(
            admin_user, history_id=first.history_id
        )

        assert language == "en"
        assert _rows() == {"en": "One"}
        assert self._history()[-1].history_change_reason == "Restored"

    def test_restoring_a_reset_removes_the_override(self, stub, admin_user):
        _save(admin_user, en="One")
        services.reset_text(admin_user, **TAGLINE)
        _save(admin_user, en="Three")
        reset = next(h for h in self._history() if h.history_type == "-")

        services.restore_text(admin_user, history_id=reset.history_id)

        assert _rows() == {}

    def test_restoring_recreates_a_deleted_override(self, stub, admin_user):
        _save(admin_user, en="One")
        services.reset_text(admin_user, **TAGLINE)
        created = self._history()[0]

        services.restore_text(admin_user, history_id=created.history_id)

        assert _rows() == {"en": "One"}

    def test_a_restored_value_is_checked_again(self, stub, admin_user):
        _save(admin_user, en="One")
        _save(admin_user, en="Two")
        first = self._history()[0]
        stub.verdict = lambda *a: ("placeholder_mismatch", "now needs {x}")

        with pytest.raises(ValidationError) as caught:
            services.restore_text(admin_user, history_id=first.history_id)

        assert caught.value.code == "placeholder_mismatch"
        assert _rows() == {"en": "Two"}

    def test_an_unknown_history_entry_is_not_found(self, stub, admin_user):
        with pytest.raises(Http404):
            services.restore_text(admin_user, history_id=999999)


def test_a_locked_text_is_never_stored(stub, admin_user):
    stub.verdict = lambda *a: ("locked_key", "locked")

    result = services.save_texts(
        admin_user,
        namespace=COOKIES,
        key="banner",
        values={"en": "Our own cookie text"},
    )

    assert result.errors["en"].code == "locked_key"
    assert TextOverride.objects.count() == 0
