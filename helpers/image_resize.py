from io import BytesIO
from typing import Union

from django.core.files import File
from django.db.models.fields.files import ImageFieldFile
from PIL import Image


def make_thumbnail(image: Union[ImageFieldFile, File], size: tuple) -> File:
    if image:
        img = Image.open(image)
        img.convert("RGB")
        img.thumbnail(size)
        thumb_io = BytesIO()

        if img.mode == "JPEG":
            img.save(thumb_io, "JPEG", quality=95)
        elif img.mode in ["RGBA", "P"]:
            fill_color = (255, 255, 255, 0)
            background = Image.new(img.mode[:-1], img.size, fill_color)
            background.paste(img, img.split()[-1])
            img = background
            img.save(thumb_io, "JPEG", quality=95)
        else:
            img.save(thumb_io, "JPEG", quality=95)

        # Construct the File object for the thumbnail
        thumbnail_name = image.name.split("/")[-1]  # Extract the filename
        thumbnail = File(thumb_io, name=thumbnail_name)

        return thumbnail
