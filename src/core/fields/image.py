"""The model field of every uploaded image (raster or SVG).

With ``width_field``/``height_field`` it records the size a browser shows
the image at: EXIF orientation applied, as the media service
(grooveshop-media-stream) applies it before resizing. The storefront states
the size the media service serves from it (og:image:width/height, a
schema.org ImageObject), so it must be the displayed one, not the stored
one Django's own ``ImageField`` records. An SVG has no raster size: both
are null.

The size is measured when a file is assigned that differs from the one the
row holds (an upload, on save, or another stored file), and on a save of a
row that has a file and no size yet (one created with a stored file's
name). Never when a row is loaded (Django's ``ImageField`` opens the stored
file of every row loaded with a null size, which an SVG always has) and
never for a reassignment of the same file (``refresh_from_db``). Rows
stored before a model gained the fields are measured by ``manage.py
backfill_image_dimensions``. A queryset ``update()`` of the file bypasses
the field: it has to write the size itself.
"""

from __future__ import annotations

from typing import IO, Any

from django.core.exceptions import SuspiciousFileOperation
from django.db.models import Model
from django.db.models.fields.files import (
    FieldFile,
    FileDescriptor,
    ImageField,
    ImageFileDescriptor,
)
from django.forms import Field
from PIL import Image, UnidentifiedImageError

from core.forms import image
from core.utils.rich_text_images import displayed_size


def displayed_dimensions(file: IO[bytes]) -> tuple[int, int] | None:
    """``(width, height)`` the media service shows the raster *file* at, or
    ``None`` for a file Pillow cannot read as an image (an SVG) or cannot
    read at all (truncated, corrupt): no size is truer than a wrong one.

    Reads the header only; the file's position is restored. A PNG's EXIF
    orientation counts only when its ``eXIf`` chunk precedes the pixel data:
    libvips (the media service's Sharp) ignores one after it, and finding
    one there would make Pillow decode the whole image.
    """
    position = file.tell() if file.seekable() else None
    try:
        file.seek(0)
        with Image.open(file) as opened:
            if opened.format == "PNG" and "exif" not in opened.info:
                return opened.size
            return displayed_size(opened)
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        return None
    finally:
        if position is not None:
            file.seek(position)


def _stored_name(value: Any) -> str | None:
    """The storage name *value* refers to, or ``None`` for an upload that
    is not stored yet (or no file)."""
    if isinstance(value, str):
        return value
    if isinstance(value, FieldFile) and value._committed:
        return value.name
    return None


class DisplayedSizeDescriptor(ImageFileDescriptor):
    """Measures on an assignment that changes the file (see the module)."""

    def __set__(self, instance: Model, value: Any) -> None:
        previous = instance.__dict__.get(self.field.attname)
        FileDescriptor.__set__(self, instance, value)
        # ``previous`` is None only inside ``Model.__init__``: a row being
        # loaded, or built with an upload that its save measures.
        if previous is None:
            return
        name = _stored_name(value)
        if name is not None and name == _stored_name(previous):
            return
        self.field.update_dimension_fields(instance, force=True)


class ImageAndSvgField(ImageField):
    descriptor_class = DisplayedSizeDescriptor

    def formfield(
        self,
        form_class: type[Field] | None = None,
        choices_form_class: type[Field] | None = None,
        **kwargs: Any,
    ) -> Field | None:
        if form_class is None:
            form_class = image.ImageAndSvgField

        return super().formfield(
            form_class=form_class,
            choices_form_class=choices_form_class,
            **kwargs,
        )

    def pre_save(self, model_instance: Model, add: bool) -> Any:
        file = super().pre_save(model_instance, add)
        if (
            file
            and self.width_field
            and self.height_field
            and getattr(model_instance, self.width_field) is None
            and getattr(model_instance, self.height_field) is None
        ):
            self.update_dimension_fields(model_instance, force=True)
        return file

    def update_dimension_fields(
        self, instance: Model, force: bool = False, *args: Any, **kwargs: Any
    ) -> None:
        """Record the displayed size of the file just assigned.

        Django connects this to ``post_init`` (``force=False``), which is
        skipped: a loaded row already holds its size.
        """
        if not force or not (self.width_field or self.height_field):
            return
        if self.attname not in instance.__dict__:
            return
        file = getattr(instance, self.attname)
        size = self._measure(file) if file else None
        width, height = size if size is not None else (None, None)
        if self.width_field:
            setattr(instance, self.width_field, width)
        if self.height_field:
            setattr(instance, self.height_field, height)

    @staticmethod
    def _measure(file: FieldFile) -> tuple[int, int] | None:
        """An upload not stored yet is read from its content; a stored file
        from storage. A stored file that is not there has no size to record:
        the row keeps none, and the save that names it still succeeds
        (``backfill_image_dimensions`` reports such rows)."""
        if not file._committed:
            return displayed_dimensions(file.file)
        try:
            with file.storage.open(file.name) as stored:
                return displayed_dimensions(stored)
        except FileNotFoundError, SuspiciousFileOperation:
            return None
