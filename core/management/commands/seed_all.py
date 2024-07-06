import importlib
import os
import pkgutil
import time
import traceback
from types import ModuleType

from django.apps import apps
from django.core.management.base import BaseCommand

DEFAULT_COUNT = 10


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

    def handle(self, *args, **options):
        start_total_time = time.time()
        count = options["count"]
        model_counts = self.parse_model_counts(options["model_counts"])
        available_models = self.get_available_models()
        success_messages = []

        for model_name in model_counts.keys():
            if model_name not in available_models:
                self.stdout.write(
                    self.style.WARNING(
                        f"Warning: Model '{model_name}' does not exist. "
                        f"Available models are: {', '.join(available_models)}"
                    )
                )

        factory_modules = self.find_factory_modules()

        for module_path in factory_modules:
            try:
                if not isinstance(module_path, str):
                    continue

                module = importlib.import_module(module_path)
                factory_classes = self.get_factory_classes(module)

                for factory_class in factory_classes:
                    model_name = factory_class._meta.model.__name__
                    model_count = model_counts.get(model_name, count)

                    start_time = time.time()
                    created_count = 0
                    for _ in range(model_count):
                        try:
                            instance = factory_class()
                            if instance:
                                created_count += 1
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Failed to create {model_name} instance: {str(e)}"))

                    end_time = time.time()
                    time_taken = end_time - start_time
                    msg = (
                        f'Successfully seeded "{model_name}" '
                        f"with {created_count}/{model_count} records in {time_taken:.2f} seconds"
                    )
                    success_messages.append(self.style.SUCCESS(msg))

            except Exception as e:
                tb = traceback.format_exc()
                error_message = f"Failed to seed using {module_path}: {str(e)}\n{tb}"
                self.stdout.write(self.style.ERROR(error_message))

        end_total_time = time.time()
        total_time_taken = end_total_time - start_total_time
        total_msg = f"Total seeding time: {total_time_taken:.2f} seconds"

        # Print all success messages at the end
        for message in success_messages:
            self.stdout.write(message)

        self.stdout.write(self.style.SUCCESS(total_msg))

    def parse_model_counts(self, model_counts_str: str) -> dict[str, int]:
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

    def find_factory_modules(self) -> list[str]:
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
    def get_factory_classes(module: ModuleType) -> list:
        factory_classes = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, "_meta") and hasattr(attr._meta, "model"):
                if attr._meta.model is not None and "Factory" in attr.__name__:
                    factory_classes.append(attr)
        return factory_classes

    def get_available_models(self) -> list[str]:
        available_models = []
        factory_modules = self.find_factory_modules()
        for module_path in factory_modules:
            module = importlib.import_module(module_path)
            factory_classes = self.get_factory_classes(module)
            for factory_class in factory_classes:
                model_name = factory_class._meta.model.__name__
                if not model_name.endswith("Translation"):
                    available_models.append(model_name)
        return available_models
