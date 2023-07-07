import os
import random

from app.settings import BASE_DIR
from tip.models import Tip
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import BaseCommand
from django.utils.timezone import now
from faker import Faker


class Command(BaseCommand):
    def handle(self, *args, **options):
        faker = Faker()

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
                title=faker.text(20),
                content=faker.text(50),
                kind=random.choice(tip_kinds),
                icon=img,
                url=settings.APP_BASE_URL,
                created_at=now(),
                active=True,
            )
        self.stdout.write(self.style.SUCCESS("Success"))
