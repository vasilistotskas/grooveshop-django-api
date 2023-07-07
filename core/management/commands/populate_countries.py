import os

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import BaseCommand

from app.settings import BASE_DIR
from country.models import Country
from region.models import Region


class Command(BaseCommand):
    def handle(self, *args, **options):
        img = "uploads/country/no_photo.jpg"
        if not default_storage.exists(img):
            img_path = os.path.join(BASE_DIR, "files/images") + "/no_photo.jpg"
            img = SimpleUploadedFile(
                name="no_photo.jpg",
                content=open(img_path, "rb").read(),
                content_type="image/jpeg",
            )

        try:
            country_gr = Country.objects.get(
                alpha_2="GR",
            )
        except Country.DoesNotExist:
            country_gr = Country(
                name="Greece",
                alpha_2="GR",
                alpha_3="GRC",
                iso_cc=300,
                phone_code=30,
                image_flag=img,
            )
            country_gr.save()

            i = 1
            for _ in range(2):
                Region.objects.create(
                    name="Greece Region" + str(i),
                    alpha="GR" + str(i),
                    alpha_2_id=country_gr.alpha_2,
                )
                i += i

        try:
            Country.objects.get(
                alpha_2="CY",
            )
        except Country.DoesNotExist:
            country_cy = Country(
                name="Cyprus",
                alpha_2="CY",
                alpha_3="CYP",
                iso_cc=196,
                phone_code=357,
                image_flag=img,
            )
            country_cy.save()
            i = 1

            for _ in range(2):
                Region.objects.create(
                    name="CY Region" + str(i),
                    alpha="CY" + str(i),
                    alpha_2_id=country_cy.alpha_2,
                )
                i += i

        self.stdout.write(self.style.SUCCESS("Success"))
