from django.core.management.base import BaseCommand

from core.tasks import set_product_search_document_values
from core.tasks import set_product_search_vector_values


class Command(BaseCommand):
    help = "Populate search indexes."

    def handle(self, *args, **options):
        self.stdout.write("Updating products")
        set_product_search_vector_values.delay()
        set_product_search_document_values.delay()
