"""An upload over its cap is shrunk, not refused; a position is always removed.

``core.uploads.prepare_servable_image`` is what every upload path calls.
"""

from __future__ import annotations

import io
import os

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import ExifTags, Image, ImageCms, features

from core import uploads
from core.uploads import prepare_servable_image
from core.utils.rich_text_images import displayed_size

GPS = {
    1: "N",
    2: (37.0, 58.0, 0.0),
    3: "E",
    4: (23.0, 43.0, 0.0),
}
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def noise(size: tuple[int, int], mode: str = "RGB") -> Image.Image:
    channels = len(Image.new(mode, (1, 1)).getbands())
    return Image.frombytes(mode, size, os.urandom(size[0] * size[1] * channels))


def encoded(
    image: Image.Image,
    fmt: str,
    *,
    orientation: int = 0,
    gps: bool = False,
    icc: bool = False,
    **options,
) -> bytes:
    exif = Image.Exif()
    if orientation:
        exif[ExifTags.Base.Orientation] = orientation
    if gps:
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(GPS)
        exif[ExifTags.IFD.GPSInfo] = exif.get_ifd(ExifTags.IFD.GPSInfo)
    if len(exif):
        options["exif"] = exif.tobytes()
    if icc:
        options["icc_profile"] = SRGB
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def upload(content: bytes, name: str = "photo.bin") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, "application/octet-stream")


def reopened(file) -> Image.Image:
    file.seek(0)
    image = Image.open(io.BytesIO(file.read()))
    image.load()
    return image


def gps_of(image: Image.Image) -> dict:
    return dict(image.getexif().get_ifd(ExifTags.IFD.GPSInfo))


@pytest.fixture
def caps(monkeypatch):
    """Replace the media service's caps (contract format name -> bytes)."""

    def use(**caps: int):
        real = uploads.image_limits()
        monkeypatch.setattr(
            uploads,
            "image_limits",
            lambda: uploads.ImageLimits(
                {**real.max_file_bytes, **caps}, real.source_pixel_limit
            ),
        )

    return use


class TestAPhotoOverItsCapIsShrunk:
    def test_a_nine_megabyte_jpeg_comes_out_under_five_upright(self, settings):
        settings.UPLOAD_IMAGE_MAX_EDGE = 3000
        # Stored sideways with an orientation tag: displayed 2250 x 3000.
        source = encoded(
            noise((3000, 2250)),
            "JPEG",
            orientation=6,
            gps=True,
            icc=True,
            quality=95,
        )
        assert len(source) > 5 * 1024 * 1024
        cap = uploads.image_limits().max_file_bytes["jpeg"]

        file, image = prepare_servable_image(upload(source, "p.jpg"))

        assert file.size <= cap
        assert image.format == "jpeg"
        assert file.name == "p.jpg"
        result = reopened(file)
        width, height = displayed_size(result)
        assert height > width
        assert result.getexif().get(ExifTags.Base.Orientation) in (None, 1)
        assert gps_of(result) == {}
        assert result.info.get("icc_profile") == SRGB

    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "TIFF"])
    def test_each_format_is_shrunk_in_its_own_format(self, fmt, caps, settings):
        settings.UPLOAD_IMAGE_MAX_EDGE = 400
        source = encoded(noise((600, 450)), fmt)
        cap = len(source) // 2
        caps(**{fmt.lower(): cap})

        file, image = prepare_servable_image(upload(source))

        assert image.format == fmt.lower()
        assert file.size <= cap
        result = reopened(file)
        assert result.format == fmt
        assert max(result.size) < 600

    @pytest.mark.skipif(not features.check("avif"), reason="no AVIF encoder")
    def test_avif_is_shrunk_too(self, caps, settings):
        settings.UPLOAD_IMAGE_MAX_EDGE = 400
        source = encoded(noise((600, 450)), "AVIF")
        caps(avif=len(source) // 2)

        _file, image = prepare_servable_image(upload(source))

        assert image.format == "avif"

    def test_a_static_gif_is_shrunk(self, caps, settings):
        settings.UPLOAD_IMAGE_MAX_EDGE = 300
        source = encoded(noise((500, 400)).quantize(colors=256), "GIF")
        caps(gif=len(source) // 2)

        file, image = prepare_servable_image(upload(source))

        assert image.format == "gif"
        assert file.size <= len(source) // 2

    def test_the_pixel_budget_shrinks_instead_of_refusing(self, settings):
        settings.UPLOAD_IMAGE_MAX_PIXELS = 20_000
        source = encoded(noise((300, 200)), "PNG")

        _file, image = prepare_servable_image(upload(source))

        assert image.width * image.height <= 20_000

    def test_nothing_is_enlarged_and_a_small_one_is_untouched(self):
        source = encoded(noise((40, 30)), "PNG")
        original = upload(source)

        file, _image = prepare_servable_image(original)

        assert file is original

    def test_a_cap_no_shrinking_can_meet_still_refuses(self, caps):
        caps(png=100)
        source = encoded(noise((300, 300)), "PNG")

        with pytest.raises(ValidationError) as caught:
            prepare_servable_image(upload(source))

        assert caught.value.code == "file_too_large"


class TestAnAnimatedImageIsRefusedNotFlattened:
    def frames(self, fmt: str) -> bytes:
        first = noise((200, 150)).quantize(colors=64)
        second = noise((200, 150)).quantize(colors=64)
        buffer = io.BytesIO()
        first.save(
            buffer,
            format=fmt,
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
        return buffer.getvalue()

    @pytest.mark.parametrize("fmt", ["GIF", "WEBP"])
    def test_one_that_needs_shrinking_is_refused(self, fmt, caps):
        source = self.frames(fmt)
        caps(**{fmt.lower(): len(source) // 2})

        with pytest.raises(ValidationError) as caught:
            prepare_servable_image(upload(source))

        assert caught.value.code == "animated_image_not_normalisable"

    @pytest.mark.parametrize("fmt", ["GIF", "WEBP"])
    def test_one_within_its_cap_passes_untouched(self, fmt):
        original = upload(self.frames(fmt))

        file, _image = prepare_servable_image(original)

        assert file is original


class TestPositionIsAlwaysRemoved:
    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "TIFF"])
    def test_a_small_photo_loses_its_gps_and_keeps_its_size(self, fmt):
        source = encoded(noise((64, 48)), fmt, gps=True, icc=True)
        assert gps_of(Image.open(io.BytesIO(source))) != {}

        file, _image = prepare_servable_image(upload(source))

        result = reopened(file)
        assert gps_of(result) == {}
        assert result.size == (64, 48)
        assert result.format == fmt
        assert result.info.get("icc_profile") == SRGB

    def test_the_orientation_tag_of_a_small_photo_is_kept(self):
        source = encoded(noise((64, 48)), "JPEG", gps=True, orientation=6)

        file, image = prepare_servable_image(upload(source))

        result = reopened(file)
        assert result.getexif().get(ExifTags.Base.Orientation) == 6
        assert (image.width, image.height) == (48, 64)

    def test_an_xmp_position_is_removed(self):
        xmp = (
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
            b'exif:GPSLatitude="37,58.0N"/></rdf:RDF></x:xmpmeta>'
        )
        source = encoded(noise((64, 48)), "PNG")
        with_xmp = io.BytesIO()
        Image.open(io.BytesIO(source)).save(
            with_xmp, format="PNG", pnginfo=_xmp_info(xmp)
        )
        assert b"GPSLatitude" in with_xmp.getvalue()

        file, _image = prepare_servable_image(upload(with_xmp.getvalue()))

        file.seek(0)
        assert b"GPSLatitude" not in file.read()

    def test_a_photo_without_a_position_is_untouched(self):
        original = upload(encoded(noise((64, 48)), "JPEG", orientation=6))

        file, _image = prepare_servable_image(original)

        assert file is original

    def test_an_animated_image_with_a_position_is_refused(self):
        buffer = io.BytesIO()
        frame = noise((50, 40)).quantize(colors=16)
        exif = Image.Exif()
        exif.get_ifd(ExifTags.IFD.GPSInfo).update(GPS)
        exif[ExifTags.IFD.GPSInfo] = exif.get_ifd(ExifTags.IFD.GPSInfo)
        frame.save(
            buffer,
            format="WEBP",
            save_all=True,
            append_images=[noise((50, 40)).quantize(colors=16)],
            exif=exif.tobytes(),
        )

        with pytest.raises(ValidationError) as caught:
            prepare_servable_image(upload(buffer.getvalue()))

        assert caught.value.code == "animated_image_not_normalisable"


def _xmp_info(xmp: bytes):
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_itxt("XML:com.adobe.xmp", xmp.decode())
    return info


class TestSvgAndUnsupportedAreUnchanged:
    def test_an_svg_passes_through(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>'
        original = upload(svg, "a.svg")

        file, image = prepare_servable_image(original)

        assert file is original
        assert image.format == "svg"

    def test_a_format_that_is_not_allowed_is_refused_not_normalised(self):
        original = upload(encoded(noise((64, 48)), "PNG", gps=True))

        with pytest.raises(ValidationError) as caught:
            prepare_servable_image(original, formats=["jpeg"])

        assert caught.value.code == "unsupported_image_type"


class TestEveryUploadPathNormalises:
    """Each path takes the shrunk file, not the original."""

    @pytest.fixture(autouse=True)
    def small(self, caps, settings):
        settings.UPLOAD_IMAGE_MAX_EDGE = 300
        self.source = encoded(noise((600, 450)), "PNG")
        self.cap = len(self.source) // 2
        caps(png=self.cap)

    def test_the_admin_form_field(self):
        from core.forms.image import ImageAndSvgField

        cleaned = ImageAndSvgField().clean(upload(self.source, "a.png"))

        assert cleaned.size <= self.cap

    def test_the_tinymce_form(self):
        from core.forms import ImageUploadForm

        form = ImageUploadForm({}, {"file": upload(self.source, "a.png")})

        assert form.is_valid(), form.errors
        assert form.cleaned_data["file"].size <= self.cap

    def test_the_serializer_field(self):
        from core.api.image_field import ServableImageField

        value = ServableImageField().to_internal_value(
            upload(self.source, "a.png")
        )

        assert value.size <= self.cap

    def test_a_model_serializer_image_field_is_the_same_field(self):
        from rest_framework import serializers

        from product.models.image import ProductImage

        class Whole(serializers.ModelSerializer):
            class Meta:
                model = ProductImage
                fields = ("image",)

        field = Whole().fields["image"]

        assert type(field).__name__ == "ServableImageField"
        assert field.to_internal_value(upload(self.source, "a.png")).size <= (
            self.cap
        )
