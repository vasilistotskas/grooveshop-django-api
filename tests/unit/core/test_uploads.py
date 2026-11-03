"""``validate_servable_image``: the one rule every image upload shares."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import serializers

from core import uploads
from core.api.image_field import ServableImageField
from core.uploads import (
    PILLOW_FORMATS,
    RASTER_MIME,
    SVG,
    image_limits,
    stored_image_problem,
    validate_servable_image,
)

SVG_OK = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>'


def raster(fmt: str = "PNG", size: tuple[int, int] = (8, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format=fmt)
    return buffer.getvalue()


def upload(content: bytes, name: str = "a.bin") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


@pytest.fixture
def small_caps(monkeypatch):
    def use(**caps: int):
        real = image_limits()
        monkeypatch.setattr(
            uploads,
            "image_limits",
            lambda: uploads.ImageLimits({**real.max_file_bytes, **caps}, 10**9),
        )

    return use


class TestTheContract:
    def test_the_vendored_file_names_every_format_the_validator_knows(self):
        contract = json.loads(uploads.LIMITS_FILE.read_text(encoding="utf-8"))

        assert set(contract["maxFileBytes"]) == {*RASTER_MIME, SVG}

    def test_every_pillow_format_maps_to_a_contract_format(self):
        assert set(PILLOW_FORMATS.values()) == set(RASTER_MIME)

    def test_the_pixel_budget_never_exceeds_the_media_services(self, settings):
        settings.UPLOAD_IMAGE_MAX_PIXELS = 10**12

        assert image_limits().max_pixels == image_limits().source_pixel_limit


class TestValidateServableImage:
    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP", "GIF", "TIFF"])
    def test_a_raster_in_a_served_format_is_accepted(self, fmt):
        image = validate_servable_image(upload(raster(fmt)))

        assert image.format == PILLOW_FORMATS[fmt]
        assert image.mime == RASTER_MIME[image.format]
        assert (image.width, image.height) == (8, 4)

    def test_the_format_comes_from_the_content_not_the_name(self):
        image = validate_servable_image(upload(raster("JPEG"), "a.png"))

        assert image.format == "jpeg"

    def test_a_safe_svg_is_accepted_and_has_no_size(self):
        image = validate_servable_image(upload(SVG_OK, "a.svg"))

        assert (image.format, image.width, image.height) == (SVG, None, None)

    def test_svg_can_be_left_out(self):
        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(SVG_OK), allow_svg=False)

        assert caught.value.code == "invalid_image"

    def test_a_script_in_an_svg_is_refused(self):
        script = b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'

        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(script))

        assert caught.value.code == "svg_script_not_allowed"

    def test_a_format_pillow_reads_but_the_service_does_not_serve(self):
        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(raster("BMP")))

        assert caught.value.code == "unsupported_image_type"

    def test_formats_narrows_the_rule(self):
        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(raster("GIF")), formats=["jpeg"])

        assert caught.value.code == "unsupported_image_type"

    @pytest.mark.parametrize("content", [b"", b"not an image"])
    def test_what_is_not_an_image_is_refused(self, content):
        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(content))

        assert caught.value.code == "invalid_image"

    def test_each_format_has_its_own_byte_cap(self, small_caps):
        small_caps(png=10)

        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(raster("PNG")))
        assert caught.value.code == "file_too_large"

        assert validate_servable_image(upload(raster("JPEG"))).format == "jpeg"

    def test_the_svg_cap_is_checked_before_it_is_parsed(self, small_caps):
        small_caps(svg=10)

        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(SVG_OK))

        assert caught.value.code == "file_too_large"

    def test_the_pixel_budget_is_enforced(self, settings):
        settings.UPLOAD_IMAGE_MAX_PIXELS = 16

        with pytest.raises(ValidationError) as caught:
            validate_servable_image(upload(raster(size=(8, 4))))

        assert caught.value.code == "image_too_large"

    def test_the_file_is_rewound(self):
        file = upload(raster())

        validate_servable_image(file)

        assert file.tell() == 0


class TestServableImageField:
    class Serializer(serializers.Serializer):
        image = ServableImageField(formats=["png"])

    def test_it_accepts_what_the_rule_accepts(self):
        serializer = self.Serializer(data={"image": upload(raster("PNG"))})

        assert serializer.is_valid(), serializer.errors

    def test_it_reports_the_rules_code(self):
        serializer = self.Serializer(data={"image": upload(raster("GIF"))})

        assert not serializer.is_valid()
        assert serializer.errors["image"][0].code == "unsupported_image_type"


class TestStoredImageProblem:
    def write(self, tmp_path: Path, name: str, content: bytes) -> Path:
        path = tmp_path / name
        path.write_bytes(content)
        return path

    def test_a_servable_file_has_no_problem(self, tmp_path):
        assert (
            stored_image_problem(self.write(tmp_path, "a.png", raster()))
            is None
        )

    def test_a_file_over_its_cap_is_named(self, tmp_path, small_caps):
        small_caps(png=10)

        problem = stored_image_problem(self.write(tmp_path, "a.png", raster()))

        assert problem is not None
        assert "png" in problem

    def test_a_small_svg_has_no_problem(self, tmp_path):
        path = self.write(tmp_path, "a.svg", SVG_OK)

        assert stored_image_problem(path) is None

    def test_an_svg_over_its_cap_is_named(self, tmp_path, small_caps):
        small_caps(svg=10)

        problem = stored_image_problem(self.write(tmp_path, "a.svg", SVG_OK))

        assert problem is not None
        assert "svg" in problem

    def test_a_file_that_is_not_an_image_is_named(self, tmp_path):
        problem = stored_image_problem(self.write(tmp_path, "a.png", b"nope"))

        assert problem == "not an image the media service can read"
