from django.core.management.base import BaseCommand

from core.tasks import update_product_translation_search_documents
from core.tasks import update_product_translation_search_vectors


class Command(BaseCommand):
    help = "Populate search indexes."

    def handle(self, *args, **options):
        self.stdout.write("Updating products")
        update_product_translation_search_vectors.delay()
        update_product_translation_search_documents.delay()
