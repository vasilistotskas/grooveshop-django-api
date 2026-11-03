"""The media library: upload, validation, alt text, references, resolve."""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from builder import services
from builder.models import MediaAsset
from tenant.models import Tenant
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero

pytestmark = pytest.mark.django_db

HOME = "template:home"


def image_bytes(
    fmt: str = "PNG", size: tuple[int, int] = (40, 20), orientation: int = 0
) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", size, "red")
    options = {}
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        options["exif"] = exif
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def upload_file(name: str = "Φωτογραφία.png", **kwargs) -> SimpleUploadedFile:
    content_type = {"png": "image/png", "jpg": "image/jpeg"}.get(
        name.rsplit(".", 1)[-1], "application/octet-stream"
    )
    return SimpleUploadedFile(name, image_bytes(**kwargs), content_type)


@pytest.fixture
def client(admin):
    return api_as(admin)


def upload(client, file=None, **fields):
    return client.post(
        reverse("builder:media-list"),
        {"file": file or upload_file(), **fields},
        format="multipart",
    )


class TestUpload:
    def test_an_image_is_stored_measured_and_described(
        self, client, admin, tenant
    ):
        response = upload(
            client,
            focalX="0.25",
            focalY="0.75",
            alt='{"el": "Κόκκινο", "en": "Red"}',
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert (body["width"], body["height"]) == (40, 20)
        assert body["mime"] == "image/png"
        assert body["sizeBytes"] > 0
        assert (body["focalX"], body["focalY"]) == (0.25, 0.75)
        assert body["alt"] == {"el": "Κόκκινο", "en": "Red"}
        assert body["uploadedBy"]["id"] == admin.pk
        assert body["usedBy"] == []
        assert body["path"].startswith(
            f"media/{tenant.schema_name}/uploads/builder/fotografia"
        )
        asset = MediaAsset.objects.get(pk=body["id"])
        assert asset.file.storage.exists(asset.file.name)

    def test_defaults_are_the_middle_and_no_alt(self, client):
        body = upload(client).json()

        assert (body["focalX"], body["focalY"]) == (0.5, 0.5)
        assert body["alt"] == {"el": "", "en": ""}

    def test_the_size_is_the_one_a_browser_shows(self, client):
        rotated = upload_file(
            "turned.jpg", fmt="JPEG", size=(40, 20), orientation=6
        )

        body = upload(client, rotated).json()

        assert (body["width"], body["height"]) == (20, 40)

    @pytest.mark.parametrize(
        ("fmt", "mime"),
        [("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")],
    )
    def test_the_raster_formats_are_accepted(self, client, fmt, mime):
        file = SimpleUploadedFile("a.bin", image_bytes(fmt), "image/x")

        assert upload(client, file).json()["mime"] == mime

    @pytest.mark.parametrize(
        ("name", "content", "code"),
        [
            (
                "logo.svg",
                b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
                "invalid_image",
            ),
            ("notes.png", b"not an image at all", "invalid_image"),
            ("empty.png", b"", "empty"),
        ],
    )
    def test_what_is_not_a_raster_image_is_refused(
        self, client, name, content, code
    ):
        response = upload(client, SimpleUploadedFile(name, content))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == code
        assert not MediaAsset.objects.exists()

    def test_a_format_the_media_service_does_not_serve_is_refused(self, client):
        response = upload(
            client, SimpleUploadedFile("a.bmp", image_bytes("BMP"))
        )

        assert response.json()["code"] == "unsupported_image_type"

    def test_the_cap_is_the_media_services_for_that_format(
        self, client, limits
    ):
        limits(max_file_bytes={"png": 10, "jpeg": 10 * 1024 * 1024})
        assert upload(client).json()["code"] == "file_too_large"

        jpeg = SimpleUploadedFile("a.jpg", image_bytes("JPEG"), "image/jpeg")
        assert upload(client, jpeg).status_code == status.HTTP_201_CREATED

    def test_the_pixel_limit_is_the_media_services(self, client, limits):
        limits(source_pixel_limit=100)

        assert upload(client).json()["code"] == "image_too_large"

    @pytest.mark.parametrize(
        ("fields", "code"),
        [
            ({"focalX": "1.5"}, "invalid_focal_point"),
            ({"focalY": "-0.1"}, "invalid_focal_point"),
            ({"alt": '{"fr": "Rouge"}'}, "unknown_locale"),
            ({"alt": '["x"]'}, "invalid_alt"),
            ({"alt": '{"el": 3}'}, "invalid_alt"),
        ],
    )
    def test_focal_points_and_alt_are_checked(self, client, fields, code):
        response = upload(client, **fields)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == code
        assert not MediaAsset.objects.exists()

    def test_a_file_is_required(self, client):
        response = client.post(
            reverse("builder:media-list"), {}, format="multipart"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.json()["fields"]

    def test_staff_may_look_but_not_upload(self, admin, staff):
        api_as(admin)
        reader = api_as(staff)

        assert (
            reader.get(reverse("builder:media-list")).status_code
            == status.HTTP_200_OK
        )
        assert upload(reader).status_code == status.HTTP_403_FORBIDDEN

    def test_a_visitor_may_not_use_the_library(self):
        visitor = APIClient()

        response = visitor.get(reverse("builder:media-list"))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


class TestMaintain:
    def test_alt_and_the_focal_point_change_per_language(self, client):
        asset_id = upload(client, alt='{"el": "Κόκκινο"}').json()["id"]
        url = reverse("builder:media-detail", kwargs={"pk": asset_id})

        response = client.patch(
            url, {"alt": {"en": "Red"}, "focalX": 0.1}, format="json"
        )

        body = response.json()
        assert response.status_code == status.HTTP_200_OK
        assert body["alt"] == {"el": "Κόκκινο", "en": "Red"}
        assert (body["focalX"], body["focalY"]) == (0.1, 0.5)
        assert client.get(url).json()["alt"] == body["alt"]

    def test_the_file_cannot_be_replaced(self, client):
        asset_id = upload(client).json()["id"]
        before = MediaAsset.objects.get(pk=asset_id).file.name

        client.patch(
            reverse("builder:media-detail", kwargs={"pk": asset_id}),
            {"file": "x", "width": 1},
            format="json",
        )

        asset = MediaAsset.objects.get(pk=asset_id)
        assert asset.file.name == before
        assert asset.width == 40

    def test_the_library_lists_newest_first_with_where_each_is_used(
        self, client, admin, catalogue, features_on
    ):
        old = upload(client).json()["id"]
        new = upload(client, upload_file("new.png", size=(41, 20))).json()["id"]
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", hero(mediaId=old))),
            i18n={},
            expected_revision=0,
        )
        services.publish(admin, keys=[HOME])

        results = client.get(reverse("builder:media-list")).json()["results"]

        assert [r["id"] for r in results] == [new, old]
        assert [r["usedBy"] for r in results] == [[], [HOME]]

    def test_the_library_can_be_searched_by_file_name(self, client):
        upload(client, upload_file("red-shoes.png"))
        upload(client, upload_file("blue-hat.png"))

        response = client.get(
            reverse("builder:media-list"), {"search": "shoes"}
        )

        assert len(response.json()["results"]) == 1


class TestUsed:
    @pytest.fixture
    def published(self, client, admin, catalogue, features_on):
        asset_id = upload(client, alt='{"el": "Κόκκινο"}').json()["id"]
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", hero(mediaId=asset_id))),
            i18n={},
            expected_revision=0,
        )
        services.publish(admin, keys=[HOME])
        return asset_id

    def test_a_used_asset_cannot_be_deleted_silently(self, client, published):
        response = client.delete(
            reverse("builder:media-detail", kwargs={"pk": published})
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        body = response.json()
        assert body["code"] == "media_in_use"
        assert body["usedBy"] == [HOME]
        assert MediaAsset.objects.filter(pk=published).exists()

    def test_an_unused_asset_is_deleted_with_its_file(self, client):
        asset_id = upload(client).json()["id"]
        asset = MediaAsset.objects.get(pk=asset_id)
        storage, name = asset.file.storage, asset.file.name

        response = client.delete(
            reverse("builder:media-detail", kwargs={"pk": asset_id})
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not MediaAsset.objects.filter(pk=asset_id).exists()
        assert not storage.exists(name)

    def test_staff_cannot_delete(self, admin, staff, published):
        response = api_as(staff).delete(
            reverse("builder:media-detail", kwargs={"pk": published})
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_changing_a_used_asset_moves_the_stores_cache_generation(
        self, client, published, tenant
    ):
        before = Tenant.objects.get(pk=tenant.pk).cache_generation

        client.patch(
            reverse("builder:media-detail", kwargs={"pk": published}),
            {"alt": {"en": "Red"}},
            format="json",
        )

        assert Tenant.objects.get(pk=tenant.pk).cache_generation > before

    def test_changing_an_unused_asset_moves_nothing(self, client, tenant):
        asset_id = upload(client).json()["id"]
        before = Tenant.objects.get(pk=tenant.pk).cache_generation

        client.patch(
            reverse("builder:media-detail", kwargs={"pk": asset_id}),
            {"alt": {"en": "Red"}},
            format="json",
        )

        assert Tenant.objects.get(pk=tenant.pk).cache_generation == before


class TestAsAReference:
    def test_a_draft_may_name_an_asset_but_publish_needs_it_to_exist(
        self, admin, catalogue, features_on
    ):
        services.save_draft(
            admin,
            key=HOME,
            draft=document(("a", hero(mediaId=987654))),
            i18n={},
            expected_revision=0,
        )

        with pytest.raises(services.DocumentInvalid) as caught:
            services.publish(admin, keys=[HOME])

        assert [e["code"] for e in caught.value.detail["errors"]] == [
            "reference_not_found"
        ]


class TestResolve:
    def resolve(self, ids: str, locale: str | None = None):
        query = {"ids": ids, **({"locale": locale} if locale else {})}
        return APIClient().get(reverse("builder:media-resolve"), query)

    @pytest.fixture
    def assets(self, client, admin, catalogue, features_on):
        used = upload(client, alt='{"el": "Κόκκινο", "en": "Red"}').json()["id"]
        greek_only = upload(
            client,
            upload_file("greek.png", size=(41, 20)),
            alt='{"el": "Μόνο ελληνικά"}',
        ).json()["id"]
        unused = upload(
            client, upload_file("unused.png", size=(42, 20))
        ).json()["id"]
        services.save_draft(
            admin,
            key=HOME,
            draft=document(
                ("a", hero(mediaId=used)),
                ("b", hero("Two", mediaId=greek_only)),
            ),
            i18n={},
            expected_revision=0,
        )
        services.publish(admin, keys=[HOME])
        return used, greek_only, unused

    def test_it_answers_for_assets_the_current_release_uses(self, assets):
        used, greek_only, unused = assets

        response = self.resolve(f"{used},{greek_only},{unused},999999", "en")

        assert response.status_code == status.HTTP_200_OK
        rows = {r["id"]: r for r in response.json()["results"]}
        assert set(rows) == {used, greek_only}
        assert rows[used]["alt"] == "Red"
        assert rows[used]["width"] == 40
        assert rows[used]["path"].startswith("media/")

    def test_a_missing_translation_falls_back_to_the_default_language(
        self, assets
    ):
        used, greek_only, _ = assets

        rows = {
            r["id"]: r
            for r in self.resolve(f"{used},{greek_only}", "en").json()[
                "results"
            ]
        }

        assert rows[greek_only]["alt"] == "Μόνο ελληνικά"

    def test_it_never_lists_what_nothing_published_uses(self, assets):
        _, _, unused = assets

        assert self.resolve(str(unused)).json()["results"] == []

    @pytest.mark.parametrize("ids", ["", "abc", "1,x", "," * 3])
    def test_the_ids_must_be_numbers(self, assets, ids):
        response = self.resolve(ids)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["code"] == "invalid_ids"

    def test_the_number_of_ids_is_limited(self, assets, settings):
        settings.BUILDER_MEDIA_RESOLVE_MAX_IDS = 2

        response = self.resolve("1,2,3")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
