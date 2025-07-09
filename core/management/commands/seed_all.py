import importlib
import logging
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import factory
from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, models, transaction
from django.db.models import Model

from core.factories import CustomDjangoModelFactory, TranslationUtilities
from core.utils.dependencies import DependencyAnalyzer, FactoryOrchestrator
from core.utils.profiler import FactoryProfiler

logger = logging.getLogger(__name__)

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

languages_comma_separated = ",".join(languages)

F = TypeVar("F", bound=factory.django.DjangoModelFactory)

DEFAULT_COUNT = getattr(settings, "SEED_DEFAULT_COUNT", 10)
BATCH_SIZE = getattr(settings, "SEED_BATCH_SIZE", 10)
MAX_RETRY_ATTEMPTS = getattr(settings, "SEED_MAX_RETRY_ATTEMPTS", 3)


@dataclass
class SeedingOptions:
    """Encapsulates all seeding options for better type safety"""

    count: int = DEFAULT_COUNT
    model_counts: str | None = None
    locales: str | None = None
    market: str = "global"
    batch_size: int = BATCH_SIZE
    max_retries: int = MAX_RETRY_ATTEMPTS
    dry_run: bool = False
    apps: str | None = None
    exclude_apps: str | None = None
    models: str | None = None
    exclude_models: str | None = None
    show_dependencies: bool = False
    performance_report: bool = False
    skip_translation_validation: bool = False
    reset: bool = False
    reset_apps: str | None = None
    continue_on_error: bool = True
    debug: bool = False

    @classmethod
    def from_dict(cls, options: dict[str, Any]) -> "SeedingOptions":
        """Create SeedingOptions from command options dict"""
        return cls(**{k: v for k, v in options.items() if hasattr(cls, k)})


@dataclass
class FactoryResult:
    """Result of factory execution"""

    factory_name: str
    model_name: str
    created: int
    requested: int
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def success_rate(self) -> float:
        return (
            (self.created / self.requested * 100) if self.requested > 0 else 0.0
        )


class ResetManager:
    """Handles data reset operations"""

    def __init__(self, stdout, style):
        self.stdout = stdout
        self.style = style

    def reset_all_data(self) -> None:
        """Reset all seeded data with dependency-aware deletion order"""
        self.stdout.write(
            self.style.WARNING(
                "⚠️  This will delete ALL data. Are you sure? (yes/no): "
            )
        )

        if input().lower() != "yes":
            self.stdout.write("Reset cancelled.")
            return

        with transaction.atomic():
            models_to_reset = self._get_deletion_order()

            for model in models_to_reset:
                if self._should_reset_model(model):
                    count = model.objects.count()
                    if count > 0:
                        model.objects.all().delete()
                        self.stdout.write(
                            f"  Deleted {count} {model.__name__} records"
                        )

    def reset_apps_data(self, app_names: list[str]) -> None:
        """Reset data for specific apps"""
        for app_name in app_names:
            try:
                app_config = apps.get_app_config(app_name)
                self._reset_app_models(app_config)
            except LookupError:
                self.stdout.write(
                    self.style.ERROR(f"App '{app_name}' not found")
                )

    def _reset_app_models(self, app_config: AppConfig) -> None:
        """Reset all models in an app"""
        with transaction.atomic():
            for model in app_config.get_models():
                if self._should_reset_model(model):
                    count = model.objects.count()
                    model.objects.all().delete()
                    self.stdout.write(
                        f"  Deleted {count} {model.__name__} records from {app_config.name}"
                    )

    def _get_deletion_order(self) -> list[type[Model]]:
        all_models = [
            model
            for model in apps.get_models()
            if self._should_reset_model(model)
        ]

        model_dependencies = defaultdict(set)
        model_dependents = defaultdict(set)

        for model in all_models:
            for field in model._meta.get_fields():
                if isinstance(field, models.ForeignKey | models.OneToOneField):
                    related_model = field.related_model
                    if related_model in all_models and related_model != model:
                        model_dependencies[model].add(related_model)
                        model_dependents[related_model].add(model)

        deletion_order = []
        in_degree = {
            model: len(model_dependencies[model]) for model in all_models
        }
        queue = deque([model for model in all_models if in_degree[model] == 0])

        while queue:
            model = queue.popleft()
            deletion_order.append(model)

            for dependent in model_dependents[model]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return list(reversed(deletion_order))

    def _should_reset_model(self, model: type[Model]) -> bool:
        """Determine if a model should be reset"""
        system_apps = {
            "admin",
            "auth",
            "sessions",
            "sitescontenttypes",
            "authtoken",
            "account",
            "socialaccount",
            "mfa",
            "usersessions",
            "extra_settings",
            "knox",
        }
        print("model._meta.app_label: ", model._meta.app_label)
        return not (
            model._meta.app_label in system_apps
            or model.__name__.endswith("Translation")
        )


class FactoryDiscovery:
    """Handles factory discovery and filtering"""

    def __init__(self, stdout, style):
        self.stdout = stdout
        self.style = style

    def discover_factories(self, options: SeedingOptions) -> list[type[F]]:
        """Discover all factories matching the given options"""
        self.stdout.write(self.style.NOTICE("🔍 Discovering factories..."))

        all_factories = []
        discovery_stats = defaultdict(int)

        target_apps = self._get_target_apps(options)

        for app_config in apps.get_app_configs():
            if app_config.name not in target_apps:
                continue

            app_factories = self._discover_app_factories(app_config)
            filtered_factories = self._filter_factories(app_factories, options)

            all_factories.extend(filtered_factories)
            discovery_stats[app_config.name] = len(filtered_factories)

            if filtered_factories:
                self.stdout.write(
                    f"  📦 {app_config.name:<15}: {len(filtered_factories):>3} factories"
                )

        self.stdout.write(
            f"\n✅ Discovered {len(all_factories)} factories across {len(discovery_stats)} apps\n"
        )

        return all_factories

    def _get_target_apps(self, options: SeedingOptions) -> set[str]:
        """Get the set of apps to process"""
        all_apps = {app.name for app in apps.get_app_configs()}

        if options.apps:
            target_apps = set(options.apps.split(","))
            invalid_apps = target_apps - all_apps
            if invalid_apps:
                raise CommandError(f"Invalid apps: {invalid_apps}")
            return target_apps

        target_apps = all_apps.copy()

        if options.exclude_apps:
            exclude_apps = set(options.exclude_apps.split(","))
            target_apps -= exclude_apps

        return target_apps

    def _discover_app_factories(self, app_config: AppConfig) -> list[type[F]]:
        """Discover factories in a specific app"""
        factories = []
        app_path = Path(app_config.path)

        factories_file = app_path / "factories.py"
        if factories_file.exists():
            module_name = f"{app_config.name}.factories"
            factories.extend(self._extract_factories_from_module(module_name))

        factories_dir = app_path / "factories"
        if factories_dir.exists() and factories_dir.is_dir():
            for factory_file in factories_dir.glob("*.py"):
                if factory_file.name == "__init__.py":
                    continue

                module_name = f"{app_config.name}.factories.{factory_file.stem}"
                factories.extend(
                    self._extract_factories_from_module(module_name)
                )

        return factories

    def _extract_factories_from_module(self, module_name: str) -> list[type[F]]:
        """Extract factory classes from a module"""
        try:
            module = importlib.import_module(module_name)
            factories = []

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if self._is_valid_factory(attr):
                    factories.append(attr)

            return factories

        except ImportError as e:
            logger.warning(f"Could not import {module_name}: {e}")
            return []

    def _is_valid_factory(self, attr: Any) -> bool:
        """Check if an attribute is a valid factory class"""
        return (
            hasattr(attr, "_meta")
            and hasattr(attr._meta, "model")
            and attr._meta.model is not None
            and "Factory" in attr.__name__
            and not TranslationUtilities.is_translation_factory(attr)
        )

    def _filter_factories(
        self, factories: list[type[F]], options: SeedingOptions
    ) -> list[type[F]]:
        """Filter factories based on options"""
        if not options.models and not options.exclude_models:
            return factories

        filtered = []

        for factory_class in factories:
            model_name = factory_class._meta.model.__name__

            if options.models:
                target_models = set(options.models.split(","))
                if model_name not in target_models:
                    continue

            if options.exclude_models:
                exclude_models = set(options.exclude_models.split(","))
                if model_name in exclude_models:
                    continue

            filtered.append(factory_class)

        return filtered


class FactoryExecutor:
    """Handles factory execution with error handling and retries"""

    def __init__(self, stdout, style):
        self.stdout = stdout
        self.style = style

    def execute_factory(
        self,
        factory: type[F],
        count: int,
        options: SeedingOptions,
        execution_context: dict[str, Any],
        profiler: FactoryProfiler | None = None,
    ) -> FactoryResult:
        """Execute a factory and return results"""
        model_name = factory._meta.model.__name__
        result = FactoryResult(
            factory_name=factory.__name__,
            model_name=model_name,
            created=0,
            requested=count,
        )

        if profiler and options.performance_report:
            with profiler.profile_factory(factory, count) as metrics:
                result.created = self._create_records(
                    factory, count, options, execution_context, result, metrics
                )
        else:
            result.created = self._create_records(
                factory, count, options, execution_context, result
            )

        return result

    def _create_records(
        self,
        factory: type[F],
        count: int,
        options: SeedingOptions,
        execution_context: dict[str, Any],
        result: FactoryResult,
        metrics=None,
    ) -> int:
        """Create records in batches"""
        created_count = 0

        for batch_start in range(0, count, options.batch_size):
            batch_end = min(batch_start + options.batch_size, count)
            batch_count = batch_end - batch_start

            batch_created = self._create_batch(
                factory,
                batch_count,
                options,
                execution_context,
                result,
                metrics,
            )
            created_count += batch_created

            if count > 50:
                progress = (batch_end / count) * 100
                self.stdout.write(f"    Progress: {progress:.1f}%", ending="\r")

        return created_count

    @transaction.atomic
    def _create_batch(
        self,
        factory: type[F],
        batch_count: int,
        options: SeedingOptions,
        execution_context: dict[str, Any],
        result: FactoryResult,
        metrics=None,
    ) -> int:
        """Create a batch of records"""
        created_count = 0

        for _ in range(batch_count):
            try:
                instance = self._create_instance(factory, execution_context)

                if not options.skip_translation_validation:
                    self._validate_instance_translations(instance, factory)

                created_count += 1

                if metrics:
                    metrics.success_count += 1

            except Exception as e:
                self._handle_creation_error(e, factory, result, metrics)

                if self._should_retry(e, options):
                    retry_result = self._retry_creation(
                        factory, execution_context, options
                    )
                    if retry_result:
                        created_count += 1

        return created_count

    def _create_instance(
        self, factory: type[F], context: dict[str, Any]
    ) -> Model:
        """Create a single instance with context"""
        if (
            isinstance(factory, type)
            and issubclass(factory, CustomDjangoModelFactory)
            and hasattr(factory, "locale_aware")
            and factory.locale_aware
        ):
            self._apply_locale_context(factory, context)

        return factory.create()

    def _apply_locale_context(
        self, factory: type[F], context: dict[str, Any]
    ) -> None:
        """Apply locale-specific context to factory"""
        market = context.get("market", "global")
        # locales = context.get("locales", [])

        # This could be enhanced to actually configure the factory
        # based on market/locale settings
        logger.debug(
            f"Applying locale context for market {market} to {factory.__name__}"
        )

    def _validate_instance_translations(
        self, instance: Model, factory: type[F]
    ) -> None:
        """Validate translation completeness"""
        if TranslationUtilities.validate_translation_completeness(
            instance, factory._meta.model
        ):
            logger.debug(f"Translations validated for {instance}")
        else:
            logger.warning(f"Incomplete translations for {instance}")

    def _handle_creation_error(
        self,
        error: Exception,
        factory: type[F],
        result: FactoryResult,
        metrics=None,
    ) -> None:
        """Handle errors during instance creation"""
        error_msg = f"Failed to create {factory._meta.model.__name__}: {error}"
        logger.error(error_msg)
        result.errors.append(str(error))

        if metrics:
            metrics.error_count += 1
            metrics.errors.append(str(error))

    def _should_retry(self, error: Exception, options: SeedingOptions) -> bool:
        """Determine if creation should be retried"""
        retry_errors = (IntegrityError, ValidationError)
        return isinstance(error, retry_errors) and options.max_retries > 0

    def _retry_creation(
        self, factory: type[F], context: dict[str, Any], options: SeedingOptions
    ) -> bool:
        """Retry instance creation with exponential backoff"""
        for attempt in range(options.max_retries):
            try:
                time.sleep(0.1 * (2**attempt))
                self._create_instance(factory, context)
                logger.info(
                    f"Retry successful for {factory.__name__} on attempt {attempt + 1}"
                )
                return True

            except Exception as e:
                logger.warning(
                    f"Retry {attempt + 1} failed for {factory.__name__}: {e}"
                )

        logger.error(f"All retries failed for {factory.__name__}")
        return False


class Command(BaseCommand):
    help = "Seed all models with their factories using intelligent dependency resolution"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.profiler = FactoryProfiler()
        self.orchestrator = FactoryOrchestrator()
        self.dependency_analyzer = DependencyAnalyzer()
        self.reset_manager = ResetManager(self.stdout, self.style)
        self.factory_discovery = FactoryDiscovery(self.stdout, self.style)
        self.factory_executor = FactoryExecutor(self.stdout, self.style)

        self.session_start_time: float | None = None
        self.total_created: int = 0
        self.total_errors: int = 0
        self.factory_results: dict[str, FactoryResult] = {}

    def add_arguments(self, parser):
        """Add command arguments"""
        parser.add_argument(
            "--count",
            type=int,
            default=DEFAULT_COUNT,
            help=f"Default number of records per model (default: {DEFAULT_COUNT})",
        )

        parser.add_argument(
            "--model-counts",
            type=str,
            help="Model-specific counts: 'Product=50,Order=20,User=10'",
        )

        parser.add_argument(
            "--locales",
            type=str,
            default=languages_comma_separated,
            help="Specific locales to use (comma-separated, e.g., 'en,de,el')",
        )

        parser.add_argument(
            "--market",
            type=str,
            choices=["global", "greece", "germany", "uk"],
            default="global",
            help="Target market for localized data generation",
        )

        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE,
            help=f"Batch size for bulk operations (default: {BATCH_SIZE})",
        )

        parser.add_argument(
            "--max-retries",
            type=int,
            default=MAX_RETRY_ATTEMPTS,
            help=f"Maximum retry attempts for failed operations (default: {MAX_RETRY_ATTEMPTS})",
        )

        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show execution plan without creating records",
        )

        parser.add_argument(
            "--apps",
            type=str,
            help="Only seed specific apps (comma-separated)",
        )

        parser.add_argument(
            "--exclude-apps",
            type=str,
            help="Exclude specific apps from seeding",
        )

        parser.add_argument(
            "--models",
            type=str,
            help="Only seed specific models (comma-separated)",
        )

        parser.add_argument(
            "--exclude-models",
            type=str,
            help="Exclude specific models from seeding",
        )

        parser.add_argument(
            "--show-dependencies",
            action="store_true",
            help="Show dependency analysis report",
        )

        parser.add_argument(
            "--performance-report",
            action="store_true",
            help="Generate detailed performance report",
        )

        parser.add_argument(
            "--skip-translation-validation",
            action="store_true",
            help="Skip translation completeness validation",
        )

        parser.add_argument(
            "--reset",
            action="store_true",
            help="Clear existing data before seeding",
        )

        parser.add_argument(
            "--reset-apps",
            type=str,
            help="Reset specific apps before seeding (comma-separated)",
        )

        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            default=True,
            help="Continue seeding even if some factories fail",
        )

        parser.add_argument(
            "--debug",
            action="store_true",
            help="Show detailed error information",
        )

    def handle(self, *args, **options):
        """Main command handler"""
        try:
            self.session_start_time = time.time()
            seeding_options = SeedingOptions.from_dict(options)

            self._validate_options(seeding_options)

            if seeding_options.performance_report:
                self.profiler.start_session()

            self._print_header()

            factory_classes = self.factory_discovery.discover_factories(
                seeding_options
            )

            if not factory_classes:
                self.stdout.write(
                    self.style.WARNING("No factories found matching criteria")
                )
                return

            execution_context = self._build_execution_context(seeding_options)
            ordered_factories = self.orchestrator.get_execution_order(
                factory_classes, execution_context
            )

            if seeding_options.show_dependencies:
                self._show_dependency_report(ordered_factories)
                return

            if seeding_options.reset or seeding_options.reset_apps:
                self._handle_reset(seeding_options)

            if seeding_options.dry_run:
                self._show_execution_plan(ordered_factories, seeding_options)
                return

            self._execute_seeding(
                ordered_factories, seeding_options, execution_context
            )

            self._generate_final_report(seeding_options)

        except Exception as e:
            self._handle_fatal_error(e)
            raise CommandError(f"Seeding failed: {e}") from e

    def _print_header(self) -> None:
        """Print command header"""
        self.stdout.write(
            self.style.SUCCESS("🚀 Enhanced Grooveshop Seeding System v2.0")
        )
        self.stdout.write(
            self.style.NOTICE("=====================================\n")
        )

    def _validate_options(self, options: SeedingOptions) -> None:
        """Validate command options"""
        if options.apps and options.exclude_apps:
            apps_list = set(options.apps.split(","))
            exclude_list = set(options.exclude_apps.split(","))
            if apps_list & exclude_list:
                raise CommandError("Cannot include and exclude the same apps")

        if options.locales:
            available_locales = TranslationUtilities.get_available_languages()
            requested_locales = options.locales.split(",")
            invalid_locales = set(requested_locales) - set(available_locales)
            if invalid_locales:
                raise CommandError(
                    f"Invalid locales: {invalid_locales}. "
                    f"Available: {available_locales}"
                )

    def _build_execution_context(
        self, options: SeedingOptions
    ) -> dict[str, Any]:
        """Build execution context from options"""
        return {
            "market": options.market,
            "locales": options.locales.split(",") if options.locales else None,
            "batch_size": options.batch_size,
            "performance_mode": options.performance_report,
        }

    def _show_dependency_report(self, ordered_factories: list[type[F]]) -> None:
        """Show dependency analysis report"""
        self.stdout.write(self.style.NOTICE("📊 Dependency Analysis Report"))
        self.stdout.write("=" * 50)

        report = self.orchestrator.get_dependency_report()
        self.stdout.write(report)

    def _handle_reset(self, options: SeedingOptions) -> None:
        """Handle data reset"""
        if options.reset:
            self.stdout.write(
                self.style.WARNING("⚠️  Resetting all seeded data...")
            )
            self.reset_manager.reset_all_data()

        elif options.reset_apps:
            reset_apps = options.reset_apps.split(",")
            self.stdout.write(
                self.style.WARNING(f"⚠️  Resetting data for apps: {reset_apps}")
            )
            self.reset_manager.reset_apps_data(reset_apps)

    def _show_execution_plan(
        self, ordered_factories: list[type[F]], options: SeedingOptions
    ) -> None:
        """Show execution plan for dry run with custom seeding info"""
        self.stdout.write(self.style.NOTICE("📋 Execution Plan (Dry Run)"))
        self.stdout.write("=" * 50)

        model_counts = self._parse_model_counts(options.model_counts)
        default_count = options.count

        total_records = 0
        custom_seeding_factories = []

        for i, factory_class in enumerate(ordered_factories, 1):
            model_name = factory_class._meta.model.__name__

            if self._uses_custom_seeding(factory_class):
                custom_seeding_factories.append(factory_class)
                self.stdout.write(
                    f"{i:>3}. {factory_class.__name__:<30} → [CUSTOM SEEDING] {model_name}"
                )

                if hasattr(factory_class, "get_seeding_description"):
                    self.stdout.write(
                        f"     └─ {factory_class.get_seeding_description()}"
                    )
            else:
                count = model_counts.get(model_name, default_count)
                total_records += count
                self.stdout.write(
                    f"{i:>3}. {factory_class.__name__:<30} → {count:>4} {model_name} records"
                )

        self.stdout.write(
            f"\n📈 Total standard records to create: {total_records}"
        )
        self.stdout.write(
            f"📦 Factories with custom seeding: {len(custom_seeding_factories)}"
        )

    def _execute_seeding(
        self,
        ordered_factories: list[type[F]],
        options: SeedingOptions,
        execution_context: dict[str, Any],
    ) -> None:
        """Execute the seeding process with support for custom seeding strategies"""
        self.stdout.write(self.style.NOTICE("🌱 Starting seeding process..."))

        model_counts = self._parse_model_counts(options.model_counts)
        total_factories = len(ordered_factories)

        for i, factory_class in enumerate(ordered_factories, 1):
            self.stdout.write(
                f"\n[{i}/{total_factories}] Processing {factory_class.__name__}..."
            )

            try:
                if self._uses_custom_seeding(factory_class):
                    result = self._execute_custom_seeding(
                        factory_class, options, execution_context
                    )
                else:
                    result = self._process_factory(
                        factory_class, model_counts, options, execution_context
                    )

                self.total_created += result.created
                self.factory_results[factory_class.__name__] = result

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✅ Created {result.created}/{result.requested} {result.model_name} records"
                    )
                )

            except Exception as e:
                self._handle_factory_error(factory_class, e, options)
                if not options.continue_on_error:
                    raise

    def _uses_custom_seeding(self, factory_class: type[F]) -> bool:
        """
        Check if a factory uses custom seeding.

        Checks in order:
        1. Has use_custom_seeding = True attribute
        2. Has custom_seed() method
        3. Is registered in SeedingStrategyRegistry
        """
        if (
            hasattr(factory_class, "use_custom_seeding")
            and factory_class.use_custom_seeding
        ):
            return True

        if hasattr(factory_class, "custom_seed") and callable(
            getattr(factory_class, "custom_seed")
        ):
            return True

        from core.factories import SeedingStrategyRegistry

        if SeedingStrategyRegistry.has_strategy(factory_class.__name__):
            return True

        return False

    def _execute_custom_seeding(
        self,
        factory_class: type[F],
        options: SeedingOptions,
        execution_context: dict[str, Any],
    ) -> FactoryResult:
        """Execute custom seeding for a factory"""
        model_name = factory_class._meta.model.__name__

        if hasattr(factory_class, "get_seeding_description"):
            description = factory_class.get_seeding_description()
            self.stdout.write(f"  ℹ️  {description}")

        custom_kwargs = {
            "verbose": options.debug,
            "batch_size": options.batch_size,
            "execution_context": execution_context,
            **execution_context,
        }

        try:
            if hasattr(factory_class, "custom_seed"):
                seeding_result = factory_class.custom_seed(**custom_kwargs)

                result = FactoryResult(
                    factory_name=factory_class.__name__,
                    model_name=model_name,
                    created=seeding_result.created_count,
                    requested=seeding_result.total_processed,
                    errors=seeding_result.errors,
                )

                if seeding_result.skipped_count > 0:
                    self.stdout.write(
                        f"  ℹ️  Skipped {seeding_result.skipped_count} existing records"
                    )

            else:
                from core.factories import SeedingStrategyRegistry

                strategy = SeedingStrategyRegistry.get_strategy(
                    factory_class.__name__
                )

                if strategy:
                    created_count = strategy(factory_class, **custom_kwargs)
                    result = FactoryResult(
                        factory_name=factory_class.__name__,
                        model_name=model_name,
                        created=created_count,
                        requested=created_count,
                    )
                else:
                    raise ValueError(
                        f"No custom seeding implementation found for {factory_class.__name__}"
                    )

            return result

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"  ❌ Custom seeding failed: {str(e)}")
            )
            raise

    def _process_factory(
        self,
        factory: type[F],
        model_counts: dict[str, int],
        options: SeedingOptions,
        execution_context: dict[str, Any],
    ) -> FactoryResult:
        """Process a single factory"""
        model_name = factory._meta.model.__name__
        count = model_counts.get(model_name, options.count)

        result = self.factory_executor.execute_factory(
            factory, count, options, execution_context, self.profiler
        )

        return result

    def _handle_factory_error(
        self, factory: type[F], error: Exception, options: SeedingOptions
    ) -> None:
        """Handle errors during factory processing"""
        error_msg = f"❌ Failed processing {factory.__name__}: {error}"
        self.stdout.write(self.style.ERROR(error_msg))

        if options.debug:
            self.stdout.write(traceback.format_exc())

        self.total_errors += 1

    def _parse_model_counts(
        self, model_counts_str: str | None
    ) -> dict[str, int]:
        """Parse model-specific counts from string"""
        if not model_counts_str:
            return {}

        model_counts = {}

        for pair in model_counts_str.split(","):
            try:
                model_name, count = pair.split("=")
                model_counts[model_name.strip()] = int(count.strip())
            except ValueError:
                self.stdout.write(
                    self.style.ERROR(f"Invalid model count format: {pair}")
                )

        return model_counts

    def _generate_final_report(self, options: SeedingOptions) -> None:
        """Generate final execution report"""
        session_duration = time.time() - self.session_start_time

        self.stdout.write(
            self.style.SUCCESS("\n🎉 Seeding completed successfully!")
        )
        self.stdout.write("=" * 50)

        self.stdout.write("📊 Session Statistics:")
        self.stdout.write(f"  • Duration: {session_duration:.2f}s")
        self.stdout.write(f"  • Total Records Created: {self.total_created}")
        self.stdout.write(f"  • Total Errors: {self.total_errors}")
        self.stdout.write(
            f"  • Success Rate: {self._calculate_success_rate():.1f}%"
        )
        self.stdout.write(
            f"  • Average Rate: {self.total_created / session_duration:.1f} records/second"
        )

        if self.factory_results:
            self.stdout.write("\n📈 Factory Results:")
            for factory_name, result in self.factory_results.items():
                self.stdout.write(
                    f"  • {factory_name:<30}: {result.created:>4}/{result.requested:<4} "
                    f"{result.model_name} ({result.success_rate:.1f}%)"
                )

                if result.errors and options.debug:
                    for error in result.errors[:3]:
                        self.stdout.write(f"    ❌ {error}")
                    if len(result.errors) > 3:
                        self.stdout.write(
                            f"    ... and {len(result.errors) - 3} more errors"
                        )

        if options.performance_report and hasattr(self.profiler, "metrics"):
            self.stdout.write("\n⚡ Performance Report:")
            self.stdout.write(self.profiler.generate_report())

    def _calculate_success_rate(self) -> float:
        """Calculate overall success rate"""
        total_attempted = self.total_created + self.total_errors
        return (
            (self.total_created / total_attempted * 100)
            if total_attempted > 0
            else 0
        )

    def _handle_fatal_error(self, error: Exception) -> None:
        """Handle fatal errors"""
        self.stdout.write(
            self.style.ERROR(f"\n💥 Fatal error during seeding: {error}")
        )

        logger.error(
            "Fatal seeding error",
            exc_info=True,
            extra={
                "session_duration": time.time() - self.session_start_time
                if self.session_start_time
                else 0,
                "total_created": self.total_created,
                "total_errors": self.total_errors,
            },
        )
