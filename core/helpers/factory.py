import importlib

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

_factory_cache = {}


def get_or_create_instance(app_label, model_name, factory_module_path, factory_class_name):
    """
    Retrieve a random instance of the specified model if it exists.

    Otherwise, create a new instance using the specified factory.

    Args:
        app_label (str): The Django app label.
        model_name (str): The name of the model.
        factory_module_path (str): The dot-path to the factory module.
        factory_class_name (str): The name of the factory class.

    Returns
        models.Model: An instance of the specified model.
    """
    try:
        Model = apps.get_model(app_label, model_name)  # noqa
    except LookupError:
        raise ImproperlyConfigured(f"Model '{model_name}' not found in app '{app_label}'.")

    if Model.objects.exists():
        return Model.objects.order_by("?").first()
    else:
        cache_key = f"{factory_module_path}.{factory_class_name}"
        if cache_key in _factory_cache:
            factory_class = _factory_cache[cache_key]
        else:
            try:
                factory_module = importlib.import_module(factory_module_path)
                factory_class = getattr(factory_module, factory_class_name)
                _factory_cache[cache_key] = factory_class
            except ModuleNotFoundError:
                raise ImproperlyConfigured(f"Factory module '{factory_module_path}' not found.")
            except AttributeError:
                raise ImproperlyConfigured(
                    f"Factory class '{factory_class_name}' not found in '{factory_module_path}'."
                )

        return factory_class.create()
