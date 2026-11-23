"""The model field of every uploaded image (raster or SVG).

With ``width_field``/``height_field`` it records the size a browser shows
the image at: EXIF orientation applied, as the media service
(grooveshop-media-stream) applies it before resizing. The storefront states
the size the media service serves from it (og:image:width/height, a
schema.org ImageObject), so it must be the displayed one, not the stored
one Django's own ``ImageField`` records. An SVG has no raster size: both
are null.

The size is measured when a file is assigned after ``Model.__init__`` whose
stored name differs from the one the instance holds (an upload, on save, or
another stored file, from another instance too; also when the row's file
was NULL or deferred), and on the save that adds a row built with a file (an
upload, or a stored file's name). Only then: never when a row is loaded
(Django's ``ImageField`` opens the stored file of every row loaded with a
null size, which an SVG always has), never inside ``refresh_from_db`` or a
deferred field's load (``ImageSizeModelMixin`` reloads the stored size with
the file), never for a reassignment of the same file, and never on a later
save of a row whose file has no size (an SVG, an unreadable or missing
file): the file it names has not changed. A save whose ``update_fields``
names the image also writes its size. Django's ``FETCH_PEERS`` fetch mode
loads a deferred file for many rows by assigning their stored names, which
counts as an assignment: one header read per row (nothing here sets a fetch
mode). Rows stored before a model gained the fields are measured by
``manage.py backfill_image_dimensions``. A queryset ``update()`` of the file
bypasses the field: it has to write the size itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.core.exceptions import SuspiciousFileOperation
from django.db.models import Model
from django.db.models.fields.files import (
    FieldFile,
    FileDescriptor,
    ImageField,
    ImageFileDescriptor,
)
from django.forms import Field

from core.forms import image
from core.utils.image_size import displayed_dimensions


def _stored_name(value: Any) -> str | None:
    """The storage name *value* refers to, or ``None`` for an upload that
    is not stored yet (or no file)."""
    if isinstance(value, str):
        return value
    if isinstance(value, FieldFile) and value._committed:
        return value.name
    return None


#: Set on ``instance._state`` by ``post_init``: ``Model.__init__`` is done, so
#: an assignment from now on is a change, not the row being built or loaded.
_INITIALISED = "image_sizes_initialised"


#: Set on ``instance._state`` by ``ImageSizeModelMixin.refresh_from_db``: what
#: it assigns is the stored file, reloaded with its stored size.
_REFRESHING = "image_sizes_refreshing"


def _is_initialised(instance: Model) -> bool:
    return getattr(instance._state, _INITIALISED, False)


def _is_refreshing(instance: Model) -> bool:
    return getattr(instance._state, _REFRESHING, False)


class DisplayedSizeDescriptor(ImageFileDescriptor):
    """Measures on an assignment that changes the file (see the module)."""

    def __set__(self, instance: Model, value: Any) -> None:
        # None for a NULL or deferred file: no name to compare, so a stored
        # name assigned to such a row is measured.
        previous = instance.__dict__.get(self.field.attname)
        FileDescriptor.__set__(self, instance, value)
        # Inside ``Model.__init__`` the row is being loaded, or built with
        # a file that its save measures.
        if not _is_initialised(instance) or _is_refreshing(instance):
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
        # ``_state.adding``, not ``add``: Django first tries an UPDATE
        # (``add=False``) for a new row whose primary key is set.
        if (
            file
            and model_instance._state.adding
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

        Django connects this to ``post_init`` (``force=False``) of a model
        with a size field: it measures nothing there (a loaded row already
        holds its size) and marks ``Model.__init__`` done for the
        descriptor.
        """
        if not force:
            setattr(instance._state, _INITIALISED, True)
            return
        if not (self.width_field or self.height_field):
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
        from storage. A stored file that is not there or cannot be read (a
        directory, no permission) has no size to record:
        the row keeps none, and the save that names it still succeeds
        (``backfill_image_dimensions`` reports such rows)."""
        if not file._committed:
            return displayed_dimensions(file.file)
        try:
            with file.storage.open(file.name) as stored:
                return displayed_dimensions(stored)
        except OSError, SuspiciousFileOperation:
            return None


def with_size_fields(
    model: type[Model], update_fields: Iterable[str]
) -> frozenset[str]:
    """*update_fields* plus the size fields of every ``ImageAndSvgField`` it
    names."""
    fields = frozenset(update_fields)
    sizes = {
        size
        for field in model._meta.concrete_fields
        if isinstance(field, ImageAndSvgField)
        and fields & {field.name, field.attname}
        for size in (field.width_field, field.height_field)
        if size
    }
    return fields | sizes


class ImageSizeModelMixin(Model):
    """For a model with an ``ImageAndSvgField`` that records its size, the
    file and its size move together: a save naming the image in
    ``update_fields`` writes the size the field measured on assignment, and
    a refresh naming it (a deferred field's load among them) reloads the
    stored size with it instead of measuring the file."""

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = with_size_fields(
                type(self), update_fields
            )
        super().save(*args, **kwargs)

    def refresh_from_db(
        self,
        using: str | None = None,
        fields: Iterable[str] | None = None,
        from_queryset: Any = None,
    ) -> None:
        if fields is not None:
            fields = with_size_fields(type(self), fields)
        setattr(self._state, _REFRESHING, True)
        try:
            super().refresh_from_db(
                using=using, fields=fields, from_queryset=from_queryset
            )
        finally:
            setattr(self._state, _REFRESHING, False)
