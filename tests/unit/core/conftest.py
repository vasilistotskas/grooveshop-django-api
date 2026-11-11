"""Fixtures for the tests of ``core``."""

import pytest
from django.test.utils import isolate_apps


@pytest.fixture
def isolated_core_models():
    """Models a test defines stay out of the project's app registry.

    A model class declared inside a test registers itself with
    ``django.apps.apps`` for the rest of the process, so every later test
    on that xdist worker that walks ``apps.get_models()`` sees it
    (``tests/unit/devtools/test_anonymise_covers_every_model.py`` failed
    on ``core.TestModel`` whenever it ran after
    ``test_manager_delegation.py`` on the same worker). Inside
    ``isolate_apps`` the class registers with a throwaway registry. No app
    is named: naming ``core`` would run ``CoreConfig.ready()`` again.
    """
    with isolate_apps():
        yield
