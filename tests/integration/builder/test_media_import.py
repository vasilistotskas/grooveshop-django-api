"""The media library's de-duplication, the import of an image the store
already shows, and the resolves the editor's preview and a share link need."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from unittest import mock

import pytest
import requests
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from builder import media_services
from builder.models import MediaAsset
from tests.integration.builder.conftest import api_as
from tests.integration.builder.test_media import (
    image_bytes,
    upload,
    upload_file,
)
from tests.utils.builder import document, hero

pytestmark = pytest.mark.django_db

HOME = "template:home"
BASE = "http://storefront.test"


@pytest.fixture
def client(admin):
    return api_as(admin)


@pytest.fixture(autouse=True)
def _storefront_address(settings):
    settings.NUXT_INTERNAL_BASE_URL = BASE


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self._content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for start in range(0, len(self._content), size):
            yield self._content[start : start + size]


@contextmanager
def storefront_serves(response=None, *, raises=None):
    """``requests.get`` as the import calls it."""
    fake = mock.Mock(
        side_effect=raises,
        return_value=response or FakeResponse(content=image_bytes()),
    )
    with mock.patch("builder.media_services.requests.get", fake):
        yield fake


def import_url(client, url, **extra):
    return client.post(
        reverse("builder:media-import"), {"url": url, **extra}, format="json"
    )


class TestDeduplication:
    def test_the_same_image_twice_is_one_asset(self, client):
        first = upload(client, alt='{"el": "Πρώτο"}')
        second = upload(client, upload_file("again.png"), alt='{"el": "Άλλο"}')

        assert first.status_code == status.HTTP_201_CREATED
        assert first.json()["duplicate"] is False
        assert second.json()["duplicate"] is True
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["alt"]["el"] == "Πρώτο"
        assert MediaAsset.objects.count() == 1
        assert (
            MediaAsset.objects.get().content_hash
            == hashlib.sha256(image_bytes()).hexdigest()
        )

    def test_a_different_image_is_a_new_asset(self, client):
        upload(client)

        other = upload(client, upload_file("big.png", size=(80, 40)))

        assert other.status_code == status.HTTP_201_CREATED
        assert MediaAsset.objects.count() == 2


class TestImportingTheStorefrontsOwnFile:
    def test_a_static_file_becomes_an_asset(self, client):
        with storefront_serves() as fetch:
            response = import_url(
                client, "/img/main-banner.png", alt={"el": "Πανό"}
            )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert (body["width"], body["height"]) == (40, 20)
        assert body["alt"]["el"] == "Πανό"
        fetch.assert_called_once()
        assert fetch.call_args.args[0] == f"{BASE}/img/main-banner.png"
        assert fetch.call_args.kwargs["allow_redirects"] is False
        assert fetch.call_args.kwargs["timeout"]

    def test_the_same_file_again_is_the_asset_already_there(self, client):
        with storefront_serves():
            first = import_url(client, "/img/a.png")
            second = import_url(client, "img/other-name.png")

        assert second.json()["duplicate"] is True
        assert second.json()["id"] == first.json()["id"]

    def test_an_upload_and_an_import_of_one_image_are_one_asset(self, client):
        uploaded = upload(client)

        with storefront_serves():
            imported = import_url(client, "/img/a.png")

        assert imported.json()["duplicate"] is True
        assert imported.json()["id"] == uploaded.json()["id"]

    def test_a_redirect_is_refused_and_not_followed(self, client):
        with storefront_serves(FakeResponse(302)) as fetch:
            response = import_url(client, "/img/moved.png")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "import_redirect"
        assert fetch.call_count == 1
        assert not MediaAsset.objects.exists()

    def test_a_missing_file_is_refused(self, client):
        with storefront_serves(FakeResponse(404)):
            response = import_url(client, "/img/none.png")

        assert response.json()["code"] == "import_not_found"

    def test_what_is_not_an_image_is_refused_like_an_upload(self, client):
        with storefront_serves(FakeResponse(content=b"<script>x</script>")):
            response = import_url(client, "/img/x.png")

        assert response.json()["code"] == "invalid_image"
        assert not MediaAsset.objects.exists()

    def test_a_file_over_the_limit_is_cut_off(self, client, settings):
        settings.BUILDER_MEDIA_MAX_BYTES = 100

        with storefront_serves(FakeResponse(content=b"x" * 5000)):
            response = import_url(client, "/img/big.png")

        assert response.json()["code"] == "file_too_large"

    def test_an_unreachable_storefront_is_a_503(self, client):
        with storefront_serves(raises=requests.ConnectionError()):
            response = import_url(client, "/img/x.png")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.json()["code"] == "storefront_unavailable"

    def test_no_storefront_address_is_a_503(self, client, settings):
        settings.NUXT_INTERNAL_BASE_URL = ""

        response = import_url(client, "/img/x.png")

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


class TestImportingAnUpload:
    @pytest.fixture
    def stored(self, tenant):
        """A file already in the store's uploads, not an asset."""
        storage = MediaAsset._meta.get_field("file").storage
        name = storage.save(
            "uploads/products/p.png", ContentFile(image_bytes(size=(12, 6)))
        )
        yield name
        storage.delete(name)

    @pytest.mark.parametrize("form", ["/media/{s}/{n}", "media/{s}/{n}"])
    def test_it_is_copied_into_the_library(self, client, tenant, stored, form):
        response = import_url(
            client, form.format(s=tenant.schema_name, n=stored)
        )

        assert response.status_code == status.HTTP_201_CREATED
        asset = MediaAsset.objects.get()
        assert (asset.width, asset.height) == (12, 6)
        assert asset.file.name.startswith("uploads/builder/")
        assert asset.file.name != stored

    def test_deleting_the_asset_leaves_the_original(
        self, client, tenant, stored
    ):
        storage = MediaAsset._meta.get_field("file").storage
        body = import_url(
            client, f"/media/{tenant.schema_name}/{stored}"
        ).json()

        assert (
            client.delete(
                reverse("builder:media-detail", kwargs={"pk": body["id"]})
            ).status_code
            == status.HTTP_204_NO_CONTENT
        )

        assert storage.exists(stored)

    def test_another_stores_file_is_refused(self, client, stored):
        response = import_url(client, f"/media/some_other_store/{stored}")

        assert response.json()["code"] == "import_other_store"

    def test_only_uploads_are_imported(self, client, tenant):
        response = import_url(
            client, f"/media/{tenant.schema_name}/private/secret.png"
        )

        assert response.json()["code"] == "invalid_import_url"

    def test_a_missing_upload_is_refused(self, client, tenant):
        response = import_url(
            client, f"/media/{tenant.schema_name}/uploads/none.png"
        )

        assert response.json()["code"] == "import_not_found"


class TestWhatIsNeverFetched:
    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/x.png",
            "http://127.0.0.1:8000/admin",
            "http://169.254.169.254/latest/meta-data",
            "//evil.example/x.png",
            "file:///etc/passwd",
            "gopher://internal/",
            "javascript:alert(1)",
            "data:image/png;base64,AAAA",
        ],
    )
    def test_an_address_with_a_scheme_or_host_is_refused(self, client, url):
        with storefront_serves() as fetch:
            response = import_url(client, url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "external_url_not_allowed"
        fetch.assert_not_called()

    @pytest.mark.parametrize(
        "url",
        [
            "/img/../admin",
            "../x.png",
            "/img/./x.png",
            "/img/x.png?size=big",
            "/img/x.png#frag",
            "/img\\x.png",
            "/img/a b.png",
            "/img/x\x01.png",
            "/img/%2e%2e/x.png".replace("%2e", "\n"),
        ],
    )
    def test_a_path_that_could_reach_elsewhere_is_refused(self, client, url):
        with storefront_serves() as fetch:
            response = import_url(client, url)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_import_url"
        fetch.assert_not_called()

    def test_the_fetch_always_goes_to_the_storefront_address(self, client):
        with storefront_serves() as fetch:
            import_url(client, "@evil.example/x.png")
            import_url(client, "/@evil.example/x.png")

        for call in fetch.call_args_list:
            assert call.args[0].startswith(f"{BASE}/")


class TestRoles:
    def test_staff_who_only_look_cannot_import(self, staff, catalogue):
        with storefront_serves() as fetch:
            response = import_url(api_as(staff), "/img/a.png")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        fetch.assert_not_called()

    def test_a_visitor_cannot(self, tenant):
        response = import_url(APIClient(), "/img/a.png")

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


class TestSearching:
    def test_the_library_is_searched_by_alt_text(self, client):
        upload(client, alt='{"el": "Κόκκινο καρέκλα", "en": "Red chair"}')
        upload(
            client,
            upload_file("b.png", size=(80, 40)),
            alt='{"el": "Τραπέζι", "en": "Table"}',
        )

        found = client.get(reverse("builder:media-list"), {"search": "chair"})
        none = client.get(reverse("builder:media-list"), {"search": "sofa"})

        assert [r["alt"]["en"] for r in found.json()["results"]] == [
            "Red chair"
        ]
        assert none.json()["results"] == []


def resolve_url(name, **kwargs):
    return reverse(f"builder:{name}", kwargs=kwargs)


class TestStaffResolve:
    def test_any_asset_resolves_even_one_nobody_published(self, client):
        asset_id = upload(client, alt='{"el": "Φωτό"}').json()["id"]

        public = APIClient().get(
            resolve_url("media-resolve"), {"ids": str(asset_id)}
        )
        staff_read = client.get(
            resolve_url("media-staff-resolve"),
            {"ids": str(asset_id), "locale": "el"},
        )

        assert public.json()["results"] == []
        (row,) = staff_read.json()["results"]
        assert row["id"] == asset_id
        assert row["alt"] == "Φωτό"
        assert row["path"].startswith("media/")
        assert "no-store" in staff_read["Cache-Control"]
        assert "private" in staff_read["Cache-Control"]

    def test_staff_who_only_look_may_read(self, client, staff, catalogue):
        asset_id = upload(client).json()["id"]

        response = api_as(staff).get(
            resolve_url("media-staff-resolve"), {"ids": str(asset_id)}
        )

        assert len(response.json()["results"]) == 1

    def test_a_visitor_may_not(self, client):
        asset_id = upload(client).json()["id"]

        response = APIClient().get(
            resolve_url("media-staff-resolve"), {"ids": str(asset_id)}
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @pytest.mark.parametrize(
        "ids", ["", "x", ",".join(map(str, range(1, 400)))]
    )
    def test_the_ids_are_checked(self, client, ids):
        response = client.get(resolve_url("media-staff-resolve"), {"ids": ids})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_ids"

    def test_it_costs_a_fixed_number_of_queries(
        self, client, django_assert_max_num_queries
    ):
        ids = []
        for size in ((10, 10), (20, 10), (30, 10)):
            ids.append(
                str(
                    upload(client, upload_file("a.png", size=size)).json()["id"]
                )
            )
        url = resolve_url("media-staff-resolve")
        client.get(url, {"ids": ",".join(ids)})

        with django_assert_max_num_queries(6):
            response = client.get(url, {"ids": ",".join(ids)})

        assert len(response.json()["results"]) == 3


class TestSharedPreviewMedia:
    @pytest.fixture
    def shared(self, client, catalogue, features_on):
        """A link whose frozen home uses asset A; asset B is only in the
        library."""
        used = upload(client, alt='{"el": "Χρήση"}').json()["id"]
        unused = upload(client, upload_file("b.png", size=(80, 40))).json()[
            "id"
        ]
        client.put(
            reverse("builder:document-draft", kwargs={"key": HOME}),
            {"draft": document(("h", hero("T", mediaId=used))), "i18n": {}},
            format="json",
            headers={"If-Match": "0"},
        )
        token = client.post(
            reverse("builder:preview-link-list"),
            {"documents": [HOME]},
            format="json",
        ).json()["token"]
        return token, used, unused

    def test_only_what_the_links_documents_use_resolves(self, shared):
        token, used, unused = shared

        response = APIClient().get(
            resolve_url("preview-media", token=token),
            {"ids": f"{used},{unused}", "locale": "el"},
        )

        assert response.status_code == status.HTTP_200_OK
        (row,) = response.json()["results"]
        assert row["id"] == used
        assert row["alt"] == "Χρήση"
        assert "no-store" in response["Cache-Control"]

    def test_the_library_is_not_opened_by_a_token(self, shared):
        token, _used, unused = shared

        response = APIClient().get(
            resolve_url("preview-media", token=token), {"ids": str(unused)}
        )

        assert response.json()["results"] == []

    def test_a_token_that_opens_nothing_is_one_404(self, shared):
        _token, used, _unused = shared

        response = APIClient().get(
            resolve_url("preview-media", token="x" * 43), {"ids": str(used)}
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "preview_link_expired"

    def test_an_expired_link_resolves_nothing(self, shared):
        from datetime import timedelta

        from django.utils import timezone

        from builder.models import BuilderPreviewLink

        token, used, _unused = shared
        BuilderPreviewLink.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = APIClient().get(
            resolve_url("preview-media", token=token), {"ids": str(used)}
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_it_costs_a_fixed_number_of_queries(
        self, shared, django_assert_max_num_queries
    ):
        token, used, unused = shared
        url = resolve_url("preview-media", token=token)
        APIClient().get(url, {"ids": f"{used},{unused}"})

        with django_assert_max_num_queries(6):
            APIClient().get(url, {"ids": f"{used},{unused}"})


def test_an_upload_helper_still_returns_the_stored_pair(admin, tenant):
    stored = media_services.create_asset(
        admin,
        upload=SimpleUploadedFile("a.png", image_bytes(), "image/png"),
    )

    assert stored.created is True
    assert stored.asset.content_hash
