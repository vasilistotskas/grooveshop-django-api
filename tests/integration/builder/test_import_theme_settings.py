"""``manage.py import_theme_settings``: each store gets its theme document,
validated against its own catalogue view, without touching anything else."""

from __future__ import annotations

import contextlib
import json
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from builder import services
from builder.models import BuilderDocument, BuilderRelease
from tests.utils.builder import document, hero

pytestmark = pytest.mark.django_db

CONTRAST = json.loads(
    open(  # noqa: SIM115
        "tests/fixtures/builder/contrast-cases.json", encoding="utf-8"
    ).read()
)
GOOD, PALE_LIGHT = (
    CONTRAST["themes"][0]["settings"],
    CONTRAST["themes"][1]["settings"],
)


@pytest.fixture
def theme_catalogue(catalogue):
    catalogue.document_kinds["theme"] = {
        "schema": {"type": "object"},
        "translatable": [],
        "linkRefs": [],
        "contrast": CONTRAST["rules"],
    }
    return catalogue


@pytest.fixture
def run(tenant, tmp_path):
    """The command for the store the fixtures bind, as ``builder_store``."""

    def go(settings_, *args):
        target = tmp_path / "themes.json"
        target.write_text(
            json.dumps(
                {
                    "stores": [
                        {"schema": tenant.schema_name, "settings": settings_}
                    ]
                }
            ),
            encoding="utf-8",
        )
        out = StringIO()
        with mock.patch(
            "builder.management.commands.import_theme_settings.tenant_context",
            lambda _tenant: contextlib.nullcontext(),
        ):
            call_command(
                "import_theme_settings", str(target), *args, stdout=out
            )
        return out.getvalue()

    return go


def test_a_store_gets_its_theme_published_and_nothing_else_moves(
    admin, run, theme_catalogue, features_on
):
    services.save_draft(
        admin,
        key="template:home",
        draft=document(("a", hero("Welcome"))),
        i18n={},
        expected_revision=0,
    )
    services.publish(admin, keys=["template:home"])
    before = services.published_versions()

    output = run(GOOD)

    release = BuilderRelease.objects.order_by("-number").first()
    assert "published in release" in output
    assert set(release.versions) == {"template:home", "theme"}
    assert release.versions["template:home"] == before["template:home"]
    theme = BuilderDocument.objects.get(key="theme")
    assert theme.draft["settings"] == GOOD
    assert theme.draft_revision == theme.published_revision == 1


def test_a_dry_run_validates_and_writes_nothing(
    run, theme_catalogue, features_on
):
    output = run(GOOD, "--dry-run")

    assert "valid, would be published" in output
    assert not BuilderDocument.objects.filter(key="theme").exists()
    assert not BuilderRelease.objects.exists()


def test_a_theme_that_misses_its_contrast_is_refused_and_nothing_is_written(
    run, theme_catalogue, features_on
):
    with pytest.raises(CommandError):
        run(PALE_LIGHT)

    assert not BuilderDocument.objects.filter(key="theme").exists()
    assert not BuilderRelease.objects.exists()


def test_the_failure_names_the_rule(run, theme_catalogue, features_on):
    with pytest.raises(CommandError) as caught:
        run(PALE_LIGHT, "--dry-run")

    assert "1 store(s) failed" in str(caught.value)


def test_a_store_that_has_a_theme_is_skipped_not_overwritten(
    run, theme_catalogue, features_on
):
    run(GOOD)

    output = run(PALE_LIGHT)

    assert "skipped" in output
    assert BuilderDocument.objects.get(key="theme").draft["settings"] == GOOD


def test_an_unknown_store_stops_the_whole_file(tmp_path):
    target = tmp_path / "themes.json"
    target.write_text(
        json.dumps({"stores": [{"schema": "no_such_store", "settings": {}}]}),
        encoding="utf-8",
    )

    with pytest.raises(CommandError, match="no such store"):
        call_command("import_theme_settings", str(target))


@pytest.mark.parametrize(
    "content",
    ['{"stores": 3}', '{"stores": [{"schema": "x"}]}', "not json"],
)
def test_a_file_of_the_wrong_shape_is_refused(tmp_path, content):
    target = tmp_path / "themes.json"
    target.write_text(content, encoding="utf-8")

    with pytest.raises(CommandError):
        call_command("import_theme_settings", str(target))


class TestANewStoresTheme:
    """``documentKinds.theme.defaults`` of the store's catalogue view is the
    theme a new store of that design starts with."""

    def test_the_defaults_become_the_theme_in_their_own_release(
        self, tenant, theme_catalogue, features_on
    ):
        from builder import seeding

        theme_catalogue.document_kinds["theme"]["defaults"] = GOOD

        release = seeding.install_theme_defaults()

        assert release is not None
        assert release.note == seeding.THEME_NOTE
        assert (
            BuilderDocument.objects.get(key="theme").draft["settings"] == GOOD
        )

    def test_a_catalogue_without_defaults_installs_nothing(
        self, tenant, theme_catalogue, features_on
    ):
        from builder import seeding

        assert seeding.install_theme_defaults() is None
        assert not BuilderDocument.objects.filter(key="theme").exists()

    def test_a_second_run_changes_nothing(
        self, tenant, theme_catalogue, features_on
    ):
        from builder import seeding

        theme_catalogue.document_kinds["theme"]["defaults"] = GOOD
        seeding.install_theme_defaults()

        assert seeding.install_theme_defaults() is None
        assert BuilderRelease.objects.count() == 1

    def test_the_theme_release_does_not_make_the_store_look_worked_on(
        self, tenant, theme_catalogue, features_on
    ):
        """``seed_with_content`` (the demo store's pages) only writes on a
        store that holds nothing an editor made: the platform's own theme
        release must not count as work."""
        from builder import seeding

        seeding.seed_store()
        theme_catalogue.document_kinds["theme"]["defaults"] = GOOD
        seeding.install_theme_defaults()

        assert seeding._untouched_defaults() is not None
