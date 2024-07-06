from django.core.management.base import (
    BaseCommand,
)
from django.utils.module_loading import (
    import_string,
)


class Command(BaseCommand):
    help = "Seed a model with it's factory"

    def add_arguments(self, parser):
        parser.add_argument("factory", type=str)
        parser.add_argument("count", type=int)

    def handle(self, *args, **options):
        path, count = options.get("factory"), options.get("count")
        factory = import_string(path)
        factory.create_batch(count)
        model_name = factory._meta.model.__name__
        msg = f'Successfully seeded "{model_name}" with {count} records'
        self.stdout.write(self.style.SUCCESS(msg))
