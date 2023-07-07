from io import BytesIO

from django.core.files import File
from PIL import Image


def make_thumbnail(image, size):
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

        thumbnail = File(thumb_io, name=image.name)

        return thumbnail
