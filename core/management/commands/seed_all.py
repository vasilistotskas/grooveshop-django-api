import asyncio
import importlib
import os
import pkgutil
import time
import traceback
from types import ModuleType
from typing import cast
from typing import Dict
from typing import List
from typing import Optional
from typing import Type
from typing import TypeVar

import factory
from asgiref.sync import sync_to_async
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.db import models

DEFAULT_COUNT = settings.SEED_DEFAULT_COUNT
BATCH_SIZE = settings.SEED_BATCH_SIZE

F = TypeVar("F", bound=factory.django.DjangoModelFactory)


class Command(BaseCommand):
    help = "Seed all models with their factories"

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=DEFAULT_COUNT,
            help=f"Number of records to create for each model (default: {DEFAULT_COUNT})",
        )
        parser.add_argument(
            "--model-counts",
            type=str,
            help="Comma-separated list of model-specific counts in the form 'ModelName1=10,ModelName2=100'",
        )

    async def handle_async(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting the seeding process...\n"))
        start_total_time = time.time()
        count = options["count"]
        model_counts = self.parse_model_counts(options["model_counts"])
        available_models = await self.get_available_models()
        success_messages = []
        factory_timings: Dict[str, float] = {}

        initial_counts = await self.get_initial_counts()

        for model_name in model_counts.keys():
            if model_name not in available_models:
                self.stdout.write(
                    self.style.WARNING(
                        f"Warning: Model '{model_name}' does not exist. "
                        f"Available models are: {', '.join(available_models)}"
                    )
                )

        factory_modules = self.find_factory_modules()

        created_counts: Dict[str, int] = {}

        for module_path in factory_modules:
            if isinstance(module_path, str):
                result = await self.process_module(
                    module_path, model_counts, count, created_counts, factory_timings
                )
                if result:
                    success_messages.extend(result)

        for message in success_messages:
            self.stdout.write(message)

        total_time = time.time() - start_total_time
        self.stdout.write(self.style.NOTICE("\nSeeding process completed.\n"))

        self.stdout.write(self.style.NOTICE("Created instances summary:"))
        for factory_class, count in created_counts.items():
            timing = factory_timings.get(factory_class, 0)
            self.stdout.write(self.style.SUCCESS(f"{factory_class:<30} : {count:<5} ({timing:.2f}s)"))

        await self.print_created_counts(initial_counts)

        self.stdout.write(self.style.NOTICE(f"\nTotal seeding time: {total_time:.2f} seconds"))

    async def process_module(
        self,
        module_path: str,
        model_counts: Dict[str, int],
        default_count: int,
        created_counts: Dict[str, int],
        factory_timings: Dict[str, float],
    ) -> List[str]:
        success_messages = []
        module = importlib.import_module(module_path)
        factory_classes = self.get_factory_classes(module)
        if not factory_classes:
            return success_messages

        self.stdout.write(self.style.NOTICE(f"\nProcessing module: {module_path}"))
        try:
            for factory_class in factory_classes:
                model_name = factory_class._meta.model.__name__  # noqa
                if model_name.endswith("Translation"):
                    continue
                model_count = model_counts.get(model_name, default_count)

                self.stdout.write(
                    self.style.NOTICE(f"Running factory: {factory_class.__name__} for {model_name}")
                )

                start_time = time.time()
                await self.create_records(factory_class, model_count, created_counts)
                elapsed_time = time.time() - start_time

                factory_timings[factory_class.__name__] = elapsed_time

        except Exception as e:
            tb = traceback.format_exc()
            error_message = f"Failed to seed using {module_path}: {str(e)}\n{tb}"
            self.stdout.write(self.style.ERROR(error_message))
            success_messages.append(self.style.ERROR(error_message))

        return success_messages

    async def create_records(
        self, factory_class: Type[F], count: int, created_counts: Dict[str, int]
    ) -> int:
        factory_name = factory_class.__name__
        created_counts[factory_name] = created_counts.get(factory_name, 0)

        self.stdout.write(self.style.NOTICE(f"Creating {count} records using {factory_name}"))

        instances = []
        for i in range(count):
            try:
                instance = await sync_to_async(factory_class.create)()
                await self.save_related_objects(instance)
                instances.append(instance)

                if len(instances) >= BATCH_SIZE:
                    await self.save_batch(instances, factory_class)
                    created_counts[factory_name] += len(instances)
                    instances.clear()

            except Exception as e:
                error_message = (
                    f"Failed to create {factory_class._meta.model.__name__} instance "  # noqa
                    f"{i + 1}/{count}: {str(e)}"
                )
                self.stdout.write(self.style.ERROR(error_message))

        if instances:
            await self.save_batch(instances, factory_class)
            created_counts[factory_name] += len(instances)

        self.stdout.write(self.style.NOTICE(f"Completed creating {count} records using {factory_name}"))

        return count

    @staticmethod
    async def save_related_objects(instance: models.Model):
        for field in instance._meta.get_fields():  # noqa
            if field.is_relation and field.many_to_one:
                related_instance = await sync_to_async(getattr)(instance, field.name)
                if related_instance and not related_instance.pk:
                    await sync_to_async(related_instance.save)()

    async def save_batch(self, instances: List[models.Model], factory_class: Type[F]):
        try:
            await factory_class._meta.model.objects.abulk_create(instances, batch_size=BATCH_SIZE)  # noqa
            self.stdout.write(self.style.NOTICE(f"Saved batch of {len(instances)} records."))
        except IntegrityError:
            for instance in instances:
                try:
                    await sync_to_async(instance.save)(
                        force_insert=False, force_update=False, using=None, update_fields=None
                    )
                except IntegrityError as e:
                    self.stdout.write(self.style.ERROR(f"Failed to save instance: {str(e)}"))

    def parse_model_counts(self, model_counts_str: Optional[str]) -> Dict[str, int]:
        if not model_counts_str:
            return {}

        model_counts = {}
        pairs = model_counts_str.split(",")
        for pair in pairs:
            try:
                model_name, count = pair.split("=")
                model_counts[model_name.strip()] = int(count.strip())
            except ValueError:
                self.stdout.write(self.style.ERROR(f"Invalid format for model count: {pair}"))
        return model_counts

    def find_factory_modules(self) -> List[str]:
        factory_modules = []
        for app in apps.get_app_configs():
            app_path = app.module.__path__[0]
            for root, _, files in os.walk(app_path):
                for file in files:
                    if file == "factories.py":
                        module_path = self.path_to_module(root, file)
                        factory_modules.append(module_path)
                if "factories" in os.listdir(root):
                    factories_dir = os.path.join(root, "factories")
                    for _, name, is_pkg in pkgutil.iter_modules([factories_dir]):
                        if not is_pkg:
                            module_path = self.path_to_module(factories_dir, f"{name}.py")
                            factory_modules.append(module_path)
        return factory_modules

    @staticmethod
    def path_to_module(root: str, file: str) -> str:
        module_path = os.path.relpath(os.path.join(root, file))
        module_path = module_path.replace(os.sep, ".")
        if module_path.endswith(".py"):
            module_path = module_path[:-3]
        return module_path

    @staticmethod
    def get_factory_classes(module: ModuleType) -> List[Type[F]]:
        factory_classes = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, "_meta") and hasattr(attr._meta, "model"):  # noqa
                if attr._meta.model is not None and "Factory" in attr.__name__:  # noqa
                    factory_classes.append(cast(Type[F], attr))
        return factory_classes

    async def get_available_models(self) -> List[str]:
        available_models = []
        factory_modules = self.find_factory_modules()
        for module_path in factory_modules:
            module = importlib.import_module(module_path)
            factory_classes = self.get_factory_classes(module)
            for factory_class in factory_classes:
                model_name = factory_class._meta.model.__name__  # noqa
                if not model_name.endswith("Translation"):
                    available_models.append(model_name)
        return available_models

    @staticmethod
    async def get_initial_counts() -> Dict[str, int]:
        initial_counts = {}
        for model in apps.get_models():
            model_name = model.__name__
            if model_name.endswith("Translation"):
                continue
            count = await model.objects.acount()
            initial_counts[model_name] = count
        return initial_counts

    async def print_created_counts(self, initial_counts: Dict[str, int]):
        self.stdout.write(self.style.NOTICE("\nTotal records created in the database:"))
        for model in apps.get_models():
            model_name = model.__name__
            if model_name.endswith("Translation"):
                continue
            initial_count = initial_counts.get(model_name, 0)
            current_count = await model.objects.acount()
            created_count = current_count - initial_count
            if created_count > 0:
                self.stdout.write(self.style.SUCCESS(f"{model_name:<30} : {created_count:<5}"))

    def handle(self, *args, **options):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.handle_async(*args, **options))
        self.stdout.write(self.style.SUCCESS("Seeding process completed successfully."))
