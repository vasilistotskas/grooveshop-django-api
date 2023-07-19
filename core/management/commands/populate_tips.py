import os
import random

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import BaseCommand
from django.utils.timezone import now

from app.settings import BASE_DIR
from tip.models import Tip


class Command(BaseCommand):
    def handle(self, *args, **options):
        img = "uploads/tip/no_photo.jpg"
        if not default_storage.exists(img):
            img_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
            img = SimpleUploadedFile(
                name="no_photo.jpg",
                content=open(img_path, "rb").read(),
                content_type="image/jpeg",
            )

        tip_kinds = ["success", "info", "error", "warning"]

        for _ in range(4):
            obj, created = Tip.objects.get_or_create(
                kind=random.choice(tip_kinds),
                icon=img,
                created_at=now(),
                active=True,
            )
        self.stdout.write(self.style.SUCCESS("Success"))
