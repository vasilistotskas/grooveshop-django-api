"""``ImageAndSvgField`` records the size a browser shows an image at, from
the file it is given, and never reads a stored file to load a row.

The storefront states the media service's rendition size from these two
columns (og:image:width/height), so they must match what the media service
sees: EXIF orientation applied, nothing for an SVG.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.forms import modelform_factory
from PIL import Image, PngImagePlugin

from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPost
from core.cache.registry import get_surface
from core.management.commands.backfill_image_dimensions import (
    Command,
    backfill_current_schema,
    measured_fields,
)
from core.utils.image_size import displayed_dimensions

pytestmark = pytest.mark.django_db

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32">'
    b'<rect width="64" height="32"/></svg>'
)
#: EXIF orientation 6: stored landscape-sideways, displayed rotated 90 deg.
ROTATE_90 = 6


def _jpeg(size: tuple[int, int], orientation: int | None = None) -> bytes:
    buffer = io.BytesIO()
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    Image.new("RGB", size, "red").save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def _png(
    size: tuple[int, int], orientation: int | None = None, *, late: bool
) -> bytes:
    """A PNG whose ``eXIf`` chunk sits before ``IDAT`` (Pillow's order) or,
    with *late*, after it, where libvips ignores it."""
    buffer = io.BytesIO()
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    Image.new("RGB", size, "red").save(buffer, "PNG", exif=exif)
    data = buffer.getvalue()
    if not late:
        return data
    chunks, index = [], 8
    while index < len(data):
        length = int.from_bytes(data[index : index + 4], "big")
        chunks.append(data[index : index + 12 + length])
        index += 12 + length
    exif_chunk = next(c for c in chunks if c[4:8] == b"eXIf")
    rest = [c for c in chunks if c[4:8] != b"eXIf"]
    return data[:8] + b"".join(rest[:-1]) + exif_chunk + rest[-1]


def _upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


def _image_field():
    return BlogPost._meta.get_field("image")


def _sizes(post: BlogPost) -> tuple[int | None, int | None]:
    post.refresh_from_db()
    return post.image_width, post.image_height


def test_an_upload_is_measured_on_save():
    post = BlogPostFactory(image=_upload("cover.jpg", _jpeg((1080, 567))))

    assert _sizes(post) == (1080, 567)


def test_the_displayed_size_is_recorded_with_exif_orientation_applied():
    post = BlogPostFactory(
        image=_upload("phone.jpg", _jpeg((800, 600), ROTATE_90))
    )

    assert _sizes(post) == (600, 800)


def test_an_svg_has_no_raster_size():
    post = BlogPostFactory(image=_upload("icon.svg", SVG))

    assert post.image.name.endswith(".svg")
    assert _sizes(post) == (None, None)


def test_a_replaced_image_is_measured_again_and_a_cleared_one_forgotten():
    post = BlogPostFactory(image=_upload("a.jpg", _jpeg((1200, 630))))

    post.image = _upload("b.jpg", _jpeg((1600, 900)))
    post.save()
    assert _sizes(post) == (1600, 900)

    post.image = None
    post.save()
    assert _sizes(post) == (None, None)


def test_a_row_created_with_a_stored_file_name_is_measured_on_save():
    field = _image_field()
    name = field.storage.save(
        "uploads/blog/stored.jpg", ContentFile(_jpeg((640, 480)))
    )

    post = BlogPostFactory(image=name)

    assert _sizes(post) == (640, 480)


def test_a_save_naming_a_file_missing_from_storage_keeps_no_size():
    post = BlogPostFactory(image="uploads/blog/not-there.jpg")

    assert _sizes(post) == (None, None)


@pytest.mark.parametrize(
    ("late", "expected"),
    [(False, (600, 800)), (True, (800, 600))],
    ids=["exif-before-pixels", "exif-after-pixels-ignored-like-libvips"],
)
def test_a_png_orientation_counts_only_where_the_media_service_reads_it(
    late, expected
):
    post = BlogPostFactory(
        image=_upload("phone.png", _png((800, 600), ROTATE_90, late=late))
    )

    assert _sizes(post) == expected


def test_an_unreadable_file_has_no_size_and_its_save_succeeds():
    # Cut inside the IHDR chunk: Pillow raises OSError ("Truncated File
    # Read"), not UnidentifiedImageError.
    truncated = _png((300, 200), late=False)[:20]
    post = BlogPostFactory(image=_upload("broken.png", truncated))

    post.save()

    assert _sizes(post) == (None, None)


@pytest.mark.parametrize("late", [False, True], ids=["no-exif", "late-exif"])
def test_a_png_is_measured_without_decoding_its_pixels(late):
    """Asking Pillow for EXIF it has not met before ``IDAT`` decodes the
    whole image (64 ms and ~27 MB for 3000x3000)."""
    content = (
        _png((3000, 3000), ROTATE_90, late=True)
        if late
        else _png((3000, 3000), late=False)
    )

    with patch.object(
        PngImagePlugin.PngImageFile,
        "load",
        side_effect=AssertionError("decoded"),
    ):
        size = displayed_dimensions(io.BytesIO(content))

    assert size == (3000, 3000)


def test_a_png_cut_inside_its_pixels_keeps_its_header_size():
    content = _png((300, 200), late=False)

    assert displayed_dimensions(io.BytesIO(content[:-40])) == (300, 200)


def test_a_later_save_of_a_row_without_a_size_never_reads_its_file():
    svg = BlogPostFactory(image=_upload("icon.svg", SVG))
    row = BlogPost.objects.get(pk=svg.pk)
    storage = _image_field().storage

    with (
        patch.object(storage, "open", side_effect=AssertionError("opened")),
        patch.object(Image, "open", side_effect=AssertionError("decoded")),
    ):
        row.save()

    assert _sizes(row) == (None, None)


class TestAStoredRow:
    """A row loaded from the database, as the admin form and the API's
    update serializer change it: the descriptor measures the file assigned
    when its name differs from the loaded one, and nothing else reads it."""

    def test_a_larger_replacement_updates_the_sizes(self):
        post = BlogPostFactory(image=_upload("cover.jpg", _jpeg((1080, 567))))
        row = BlogPost.objects.get(pk=post.pk)

        row.image = _upload("cover-large.jpg", _jpeg((1600, 840)))
        row.save()

        assert _sizes(row) == (1600, 840)

    def test_a_replacement_through_a_model_form_updates_the_sizes(self):
        """The admin's path: ``ModelForm`` assigns the upload with
        ``save_form_data``."""
        post = BlogPostFactory(image=_upload("cover.jpg", _jpeg((1080, 567))))
        row = BlogPost.objects.get(pk=post.pk)
        form = modelform_factory(BlogPost, fields=["image"])(
            data={},
            files={"image": _upload("cover-large.jpg", _jpeg((1600, 840)))},
            instance=row,
        )

        assert form.is_valid(), form.errors
        form.save()

        assert _sizes(row) == (1600, 840)

    def test_another_stored_file_is_measured(self):
        post = BlogPostFactory(image=_upload("cover.jpg", _jpeg((1080, 567))))
        name = _image_field().storage.save(
            "uploads/blog/other.jpg", ContentFile(_jpeg((1200, 630)))
        )
        row = BlogPost.objects.get(pk=post.pk)

        row.image = name
        row.save()

        assert _sizes(row) == (1200, 630)

    @pytest.mark.parametrize("content", ["jpeg", "svg"])
    def test_a_save_without_a_file_change_reads_no_file(self, content):
        upload = (
            _upload("cover.jpg", _jpeg((300, 200)))
            if content == "jpeg"
            else _upload("icon.svg", SVG)
        )
        row = BlogPost.objects.get(pk=BlogPostFactory(image=upload).pk)
        before = (row.image_width, row.image_height)
        storage = _image_field().storage

        with (
            patch.object(storage, "open", side_effect=AssertionError("opened")),
            patch.object(Image, "open", side_effect=AssertionError("decoded")),
        ):
            row.image = row.image.name
            row.save()

        assert _sizes(row) == before

    def test_an_svg_replaced_by_a_raster_gets_its_size(self):
        post = BlogPostFactory(image=_upload("icon.svg", SVG))
        row = BlogPost.objects.get(pk=post.pk)

        row.image = _upload("photo.jpg", _jpeg((640, 480)))
        row.save()

        assert _sizes(row) == (640, 480)

    def test_a_raster_replaced_by_an_svg_has_no_size(self):
        post = BlogPostFactory(image=_upload("photo.jpg", _jpeg((640, 480))))
        row = BlogPost.objects.get(pk=post.pk)

        row.image = _upload("icon.svg", SVG)
        row.save()

        assert _sizes(row) == (None, None)


def test_a_file_saved_through_the_field_file_is_measured():
    """``FieldFile.save``, the avatar import's path, on a stored row."""
    post = BlogPostFactory(image=_upload("icon.svg", SVG))
    row = BlogPost.objects.get(pk=post.pk)

    row.image.save("photo.jpg", _upload("avatar", _jpeg((640, 480), ROTATE_90)))

    assert _sizes(row) == (480, 640)


def test_loading_or_refreshing_a_row_never_opens_its_file():
    """Django's own ``ImageField`` opens the stored file of every row loaded
    with a null size, which an SVG row always has."""
    svg = BlogPostFactory(image=_upload("icon.svg", SVG))
    jpeg = BlogPostFactory(image=_upload("cover.jpg", _jpeg((300, 200))))
    storage = _image_field().storage

    with (
        patch.object(storage, "open", side_effect=AssertionError("opened")),
        patch.object(Image, "open", side_effect=AssertionError("decoded")),
    ):
        rows = list(BlogPost.objects.filter(pk__in=[svg.pk, jpeg.pk]))
        for row in rows:
            row.refresh_from_db()

    assert {(row.image_width, row.image_height) for row in rows} == {
        (None, None),
        (300, 200),
    }


class TestBackfill:
    def _unmeasured(self, content: bytes, name: str):
        post = BlogPostFactory(image=_upload(name, content))
        BlogPost.objects.filter(pk=post.pk).update(
            image_width=None, image_height=None
        )
        return post

    def test_measures_rows_stored_before_the_fields_existed(self):
        post = self._unmeasured(_jpeg((800, 600), ROTATE_90), "old.jpg")

        tallies = backfill_current_schema(measured_fields())

        assert _sizes(post) == (600, 800)
        assert tallies["blog.BlogPost.image"].measured == 1

    def test_counts_svgs_and_missing_files_and_writes_nothing_for_them(self):
        svg = self._unmeasured(SVG, "icon.svg")
        gone = self._unmeasured(_jpeg((300, 200)), "gone.jpg")
        _image_field().storage.delete(gone.image.name)

        tally = backfill_current_schema(measured_fields())[
            "blog.BlogPost.image"
        ]

        assert (tally.scanned, tally.unmeasurable, tally.missing) == (2, 1, 1)
        assert _sizes(svg) == (None, None)
        assert _sizes(gone) == (None, None)

    def test_skips_measured_rows_and_a_dry_run_writes_nothing(self):
        BlogPostFactory(image=_upload("done.jpg", _jpeg((300, 200))))
        post = self._unmeasured(_jpeg((640, 480)), "todo.jpg")

        tally = backfill_current_schema(measured_fields(), dry_run=True)[
            "blog.BlogPost.image"
        ]

        assert (tally.scanned, tally.measured) == (1, 1)
        assert _sizes(post) == (None, None)

    def test_queues_the_purge_of_the_surfaces_that_render_what_it_changed(
        self,
    ):
        self._unmeasured(_jpeg((640, 480)), "todo.jpg")

        with patch(
            "core.management.commands.backfill_image_dimensions."
            "mark_surface_stale"
        ) as stale:
            backfill_current_schema(measured_fields())

        marked = {call.args[0] for call in stale.call_args_list}
        assert marked
        assert all(
            "blog.BlogPost" in get_surface(code).invalidated_by
            for code in marked
        )

    def test_a_dry_run_purges_nothing(self):
        self._unmeasured(_jpeg((640, 480)), "todo.jpg")

        with patch(
            "core.management.commands.backfill_image_dimensions."
            "mark_surface_stale"
        ) as stale:
            backfill_current_schema(measured_fields(), dry_run=True)

        stale.assert_not_called()

    def test_the_command_measures_the_current_store(self):
        post = self._unmeasured(_jpeg((640, 480)), "todo.jpg")

        out = io.StringIO()
        # The suite's connection is the store; the command refuses the
        # public schema without --tenant/--all-tenants, which
        # test_backfills_reach_suspended_stores.py covers.
        with patch.object(Command, "require_tenant_scope", False):
            call_command("backfill_image_dimensions", stdout=out)

        assert _sizes(post) == (640, 480)
        assert "measured 1 row(s)." in out.getvalue()

    def test_every_image_the_storefront_states_a_size_for_is_covered(self):
        labels = {
            f"{model._meta.label}.{field.name}"
            for model, field in measured_fields()
        }

        assert labels == {
            "blog.BlogPost.image",
            "blog.BlogCategory.image",
            "user.UserAccount.image",
            "product.ProductImage.image",
        }
