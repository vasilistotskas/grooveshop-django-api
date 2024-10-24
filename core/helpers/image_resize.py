from io import BytesIO
from typing import IO

from django.core.files import File
from PIL import Image
from PIL._typing import StrOrBytesPath  # noqa


def make_thumbnail(fp: StrOrBytesPath | IO[bytes], size: tuple[float, float]) -> File:
    if fp:
        img = Image.open(fp)
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
        thumbnail_name = fp.name.split("/")[-1]  # Extract the filename
        thumbnail = File(thumb_io, name=thumbnail_name)

        return thumbnail
