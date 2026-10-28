"""The store admin's text editor: the page and the endpoints it talks to.

Rendered through the real admin site with a store's staff logged in; the
storefront is faked where ``requests`` leaves.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import translation

from tests.utils.texts import COOKIES, FOOTER, HEADER, entry
from texts import services
from texts.factories import TextOverrideFactory
from texts.models import TextOverride

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def url(name: str = "changelist") -> str:
    # The admin's default language is Greek; these tests read English.
    with translation.override("en"):
        return reverse(f"admin:texts_textoverride_{name}")


@pytest.fixture
def client(client_and_user) -> Client:
    return client_and_user[0]


def page(client, **query):
    return client.get(url(), query)


class TestThePage:
    def test_it_lists_the_texts_with_their_originals(self, stub, client):
        response = page(client)

        assert response.status_code == 200
        html = response.content.decode()
        assert "Our shop" in html
        assert "Το κατάστημά μας" in html
        assert "tagline" in html
        assert FOOTER in html
        assert "Storefront texts" in html

    def test_it_is_in_the_store_sidebar(self, stub, client):
        html = page(client).content.decode()

        assert url() in html
        assert "Storefront Texts" in html

    def test_the_page_asks_the_storefront_for_this_stores_texts(
        self, stub, client
    ):
        page(client)

        assert stub.gets[0]["params"] == {"schema": "texts_store"}

    def test_the_stores_wording_shows_in_place_of_the_original(
        self, stub, client
    ):
        TextOverrideFactory(
            namespace=FOOTER, key="tagline", language="en", value="Ours now"
        )

        html = page(client).content.decode()

        assert ">Ours now</textarea>" in html.replace("\n", "")
        assert "Your wording" in html

    def test_a_locked_text_is_listed_but_cannot_be_edited(self, stub, client):
        html = page(client, q="banner").content.decode()

        assert "Locked" in html
        assert "manages this text" in html
        assert "disabled" in html
        assert "Save" not in html.split("</main>")[0].split("results")[-1]

    def test_an_editable_text_has_a_save_button_and_placeholder_chips(
        self, stub, client
    ):
        html = page(client, q="greeting").content.decode()

        assert ">{name}</button>" in "".join(html.split())
        assert "Save" in html

    def test_it_previews_where_the_text_shows(self, stub, client):
        html = page(client, q="tagline").content.decode()

        assert "View on the storefront" in html

    def test_a_storefront_that_cannot_answer_says_so(self, stub, client):
        stub.down = True

        response = page(client)

        assert response.status_code == 200
        assert "could not be reached" in response.content.decode()

    def test_an_anonymous_visitor_is_sent_to_log_in(self, stub, tenant):
        response = Client().get(url())

        assert response.status_code == 302

    def test_the_old_change_form_leads_to_the_editor(self, stub, client):
        row = TextOverrideFactory(namespace=FOOTER, key="tagline")

        response = client.get(
            reverse("admin:texts_textoverride_change", args=[row.pk])
        )

        assert response.status_code == 302
        assert response["Location"].startswith(
            reverse("admin:texts_textoverride_changelist")
        )

    def test_nothing_can_be_added_or_deleted_through_the_admin(
        self, stub, client
    ):
        row = TextOverrideFactory()

        assert client.get(url("add")).status_code == 403
        assert (
            client.get(
                reverse("admin:texts_textoverride_delete", args=[row.pk])
            ).status_code
            == 403
        )


class TestFindingTexts:
    def _keys(self, response) -> set[str]:
        html = response.content.decode()
        return {
            key
            for key in ("tagline", "greeting", "menu", "only_greek", "banner")
            if f'class="font-semibold text-important text-sm break-all">{key}<'
            in html
        }

    def test_search_matches_the_key(self, stub, client):
        assert self._keys(page(client, q="greet")) == {"greeting"}

    def test_search_matches_a_default_in_either_language(self, stub, client):
        assert self._keys(page(client, q="Μενού")) == {"menu"}
        assert self._keys(page(client, q="our shop")) == {"tagline"}

    def test_search_matches_what_the_store_wrote(self, stub, client):
        TextOverrideFactory(
            namespace=HEADER, key="menu", language="en", value="Browse all"
        )

        assert self._keys(page(client, q="browse")) == {"menu"}

    def test_every_word_must_match(self, stub, client):
        assert self._keys(page(client, q="our greeting")) == set()
        assert self._keys(page(client, q="hello name")) == {"greeting"}

    def test_no_match_says_so(self, stub, client):
        assert "No text matches" in page(client, q="zzzz").content.decode()

    def test_filter_by_area(self, stub, client):
        assert self._keys(page(client, area="header")) == {"menu"}
        assert self._keys(page(client, area="footer")) == {
            "tagline",
            "greeting",
            "only_greek",
        }

    def test_an_unknown_area_filters_nothing(self, stub, client):
        assert len(self._keys(page(client, area="nope"))) == 5

    def test_changed_only(self, stub, client):
        TextOverrideFactory(namespace=HEADER, key="menu", language="en")

        assert self._keys(page(client, changed="1")) == {"menu"}

    def test_missing_a_translation(self, stub, client):
        assert self._keys(page(client, missing="1")) == {"only_greek"}

    def test_a_translation_the_store_supplied_is_no_longer_missing(
        self, stub, client
    ):
        TextOverrideFactory(
            namespace=FOOTER, key="only_greek", language="en", value="Greek"
        )

        assert self._keys(page(client, missing="1")) == set()

    def test_filters_combine(self, stub, client):
        TextOverrideFactory(namespace=FOOTER, key="greeting", language="en")
        TextOverrideFactory(namespace=HEADER, key="menu", language="en")

        assert self._keys(page(client, changed="1", area="footer")) == {
            "greeting"
        }

    def test_the_results_are_paged(self, stub, client):
        stub.catalogue["entries"] = [
            entry(FOOTER, f"k{n:03}", en=f"Text {n}") for n in range(45)
        ]

        first = page(client).content.decode()
        third = page(client, page=3).content.decode()

        assert "Page 1 of 3" in first
        assert "k000" in first and "k020" not in first
        assert "k040" in third
        assert "Previous" in third and "Next" not in third

    def test_a_filter_request_from_htmx_answers_only_the_results(
        self, stub, client
    ):
        response = client.get(url(), {"q": "greet"}, **HTMX)

        html = response.content.decode()
        assert "greeting" in html
        assert "<html" not in html
        assert 'name="q"' not in html


class TestValidatingWhileTyping:
    def post(self, client, value, language="en", key="greeting"):
        return client.post(
            url("validate"),
            {
                "namespace": FOOTER,
                "key": key,
                "language": language,
                f"value_{language}": value,
            },
            **HTMX,
        )

    def test_a_good_text_is_confirmed(self, stub, client):
        html = self.post(client, "Hi {name}").content.decode()

        assert "This text is fine" in html

    @pytest.mark.parametrize(
        ("code", "fragment"),
        [
            ("unknown_key", "no longer exists"),
            ("locked_key", "managed by the platform"),
            ("syntax_error", "not valid"),
            ("placeholder_mismatch", "same placeholders"),
        ],
    )
    def test_each_code_shows_its_message_and_the_storefronts_hint(
        self, stub, client, code, fragment
    ):
        stub.verdict = lambda *a: (code, "Missing {name}")

        html = self.post(client, "Hi").content.decode()

        assert fragment in html
        assert "Missing {name}" in html

    def test_the_original_needs_no_check(self, stub, client):
        html = self.post(client, "Hello {name}").content.decode()

        assert stub.posts == []
        assert "fine" not in html and "alert" not in html

    def test_a_blank_text_is_not_asked_about(self, stub, client):
        self.post(client, "   ")

        assert stub.posts == []

    def test_too_long_is_said_without_asking(self, stub, client, settings):
        settings.TEXTS_MAX_VALUE_LENGTH = 5

        html = self.post(client, "x" * 9).content.decode()

        assert "9 of 5" in html
        assert stub.posts == []

    def test_an_unreachable_storefront_is_said_not_swallowed(
        self, stub, client
    ):
        _ = client.get(url())  # holds the catalogue
        with mock.patch(
            "texts.storefront.requests.post",
            side_effect=__import__("requests").Timeout("slow"),
        ):
            html = self.post(client, "Hi {name}").content.decode()

        assert "could not be reached" in html

    def test_it_needs_the_permission(self, stub, tenant):
        from user.factories.account import UserAccountFactory

        nobody = Client()
        user = UserAccountFactory(is_staff=True)
        nobody.force_login(
            user, backend="tenant.auth_backends.PlatformStaffBackend"
        )

        assert self.post(nobody, "Hi").status_code in (302, 403)

    def test_a_viewer_who_cannot_change_texts_is_refused(
        self, stub, client_and_user
    ):
        client, user = client_and_user

        def only_view(perm, obj=None):
            return perm != services.CHANGE_PERMISSION

        with mock.patch.object(type(user), "has_perm", side_effect=only_view):
            response = self.post(client, "Hi {name}")

        assert response.status_code == 403
        assert stub.posts == []

    def test_get_is_not_allowed(self, stub, client):
        assert client.get(url("validate")).status_code == 405


class TestSaving:
    def save(self, client, key="tagline", **values):
        data = {"namespace": FOOTER, "key": key}
        data.update({f"value_{lang}": text for lang, text in values.items()})
        return client.post(url("save"), data, **HTMX)

    def test_saving_stores_the_text_and_confirms_in_the_row(self, stub, client):
        response = self.save(client, en="Welcome", el="Καλώς")

        html = response.content.decode()
        assert response.status_code == 200
        assert "Saved." in html
        assert 'id="text-' in html
        assert {
            (r.language, r.value)
            for r in TextOverride.objects.filter(key="tagline")
        } == {("en", "Welcome"), ("el", "Καλώς")}

    def test_a_refused_text_saves_nothing_and_keeps_what_was_typed(
        self, stub, client
    ):
        stub.verdict = lambda *a: ("syntax_error", "bad brace")

        html = self.save(client, en="Welcome {").content.decode()

        assert TextOverride.objects.count() == 0
        assert "Nothing was saved" in html
        assert "bad brace" in html
        assert "Welcome {" in html

    def test_a_save_with_the_storefront_down_is_refused_visibly(
        self, stub, client
    ):
        client.get(url())
        stub.down = True
        from texts.storefront import clear_catalogue_cache

        clear_catalogue_cache()

        response = self.save(client, en="Welcome")

        assert TextOverride.objects.count() == 0
        assert response["HX-Retarget"] == "#texts-alert"
        assert "could not be reached" in response.content.decode()

    def test_a_locked_text_cannot_be_saved(self, stub, client):
        stub.verdict = lambda *a: ("locked_key", "locked")

        response = client.post(
            url("save"),
            {"namespace": COOKIES, "key": "banner", "value_en": "Mine"},
            **HTMX,
        )

        assert TextOverride.objects.count() == 0
        assert "managed by the platform" in response.content.decode()

    def test_saving_the_original_back_changes_nothing(self, stub, client):
        html = self.save(client, en="Our shop").content.decode()

        assert TextOverride.objects.count() == 0
        assert "No changes to save" in html

    def test_it_needs_the_permission(self, stub, tenant):
        from user.factories.account import UserAccountFactory

        client = Client()
        client.force_login(
            UserAccountFactory(is_staff=True),
            backend="tenant.auth_backends.PlatformStaffBackend",
        )

        response = self.save(client, en="Welcome")

        assert response.status_code in (302, 403)
        assert TextOverride.objects.count() == 0


class TestResetAndHistory:
    def test_reset_goes_back_to_the_original(self, stub, client):
        TextOverrideFactory(
            namespace=FOOTER, key="tagline", language="en", value="Ours"
        )

        response = client.post(
            url("reset"), {"namespace": FOOTER, "key": "tagline"}, **HTMX
        )

        assert TextOverride.objects.count() == 0
        assert "Back to the default" in response.content.decode()

    def test_reset_is_offered_only_for_a_changed_text(self, stub, client):
        assert (
            "Use the original" not in page(client, q="tagline").content.decode()
        )
        TextOverrideFactory(namespace=FOOTER, key="tagline", language="en")

        assert "Use the original" in page(client, q="tagline").content.decode()

    def test_history_shows_who_when_before_and_after(
        self, stub, client_and_user
    ):
        client, _user = client_and_user
        for value in ("One", "Two"):
            client.post(
                url("save"),
                {"namespace": FOOTER, "key": "tagline", "value_en": value},
                **HTMX,
            )

        response = client.get(
            url("history"), {"namespace": FOOTER, "key": "tagline"}, **HTMX
        )

        html = response.content.decode()
        assert response.status_code == 200
        assert "Created" in html and "Edited" in html
        assert "One" in html and "Two" in html
        assert "Our shop" in html  # the first change's "before"
        assert html.count("Restore this") == 1  # not on the current state

    def test_restore_puts_the_earlier_text_back(self, stub, client):
        for value in ("One", "Two"):
            client.post(
                url("save"),
                {"namespace": FOOTER, "key": "tagline", "value_en": value},
                **HTMX,
            )
        first = TextOverride.history.filter(key="tagline").order_by(
            "history_id"
        )[0]

        response = client.post(
            url("restore"),
            {
                "namespace": FOOTER,
                "key": "tagline",
                "history_id": first.history_id,
            },
            **HTMX,
        )

        assert TextOverride.objects.get(key="tagline").value == "One"
        assert "Restored." in response.content.decode()

    def test_restore_refuses_a_value_the_storefront_no_longer_accepts(
        self, stub, client
    ):
        for value in ("One", "Two"):
            client.post(
                url("save"),
                {"namespace": FOOTER, "key": "tagline", "value_en": value},
                **HTMX,
            )
        first = TextOverride.history.filter(key="tagline").order_by(
            "history_id"
        )[0]
        stub.verdict = lambda *a: ("placeholder_mismatch", "needs {x}")

        response = client.post(
            url("restore"),
            {
                "namespace": FOOTER,
                "key": "tagline",
                "history_id": first.history_id,
            },
            **HTMX,
        )

        assert TextOverride.objects.get(key="tagline").value == "Two"
        assert "same placeholders" in response.content.decode()

    def test_restore_rejects_a_bad_id(self, stub, client):
        response = client.post(
            url("restore"),
            {"namespace": FOOTER, "key": "tagline", "history_id": "x"},
            **HTMX,
        )

        assert response.status_code == 400


def test_a_user_who_may_only_view_gets_no_way_to_save(stub, client_and_user):
    client, _user = client_and_user
    with mock.patch(
        "texts.admin.TextOverrideAdmin._can_edit", return_value=False
    ):
        html = page(client, q="tagline").content.decode()

    article = html.split("<article")[1].split("</article>")[0]
    assert "do not have permission to change them" in html
    assert "disabled" in article
    assert ">save</span>" not in article


def test_the_view_permission_alone_cannot_write(stub, tenant, admin_user):
    def only_view(perm, obj=None):
        return perm != services.CHANGE_PERMISSION

    with (
        mock.patch.object(type(admin_user), "has_perm", side_effect=only_view),
        pytest.raises(PermissionDenied),
    ):
        services.reset_text(admin_user, namespace=FOOTER, key="tagline")
