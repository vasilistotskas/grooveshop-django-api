"""``manage.py persist_block_upgrades``: what is stored moves to the current
version of its blocks, validated, and nothing a store can see changes."""

from __future__ import annotations

import contextlib
from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from builder import services
from builder.models import BuilderDocument, BuilderReference, BuilderVersion
from builder.storefront import clear_catalogue_cache
from tests.utils.builder import TEXT, _schema, document, section

pytestmark = pytest.mark.django_db

HOME = "template:home"


def offers(version: int) -> dict:
    """The offers band at version 1, or at 2 with its data setting."""
    block = {
        "type": "offers_preview",
        "version": version,
        "accepts": [version],
        "translatable": ["/title"],
        "refs": {},
        "linkRefs": [],
        "limits": {},
        "children": {"allowed": []},
        "kinds": ["section"],
        "templates": ["home"],
    }
    if version == 1:
        return {
            **block,
            "schema": _schema(title=dict(TEXT), limit={"type": "integer"}),
        }
    return {
        **block,
        "schema": _schema(
            title=dict(TEXT),
            offers={
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "required": ["limit"],
                "additionalProperties": False,
            },
        ),
        "data": {
            "/offers": {
                "field": "query",
                "source": "promotion",
                "onEmpty": "hide",
                "maxLimit": 12,
            }
        },
    }


@pytest.fixture
def upgraded_catalogue(catalogue):
    """Moves the catalogue on from a store's stored documents, as a storefront
    release does."""

    def move():
        catalogue.blocks = [
            block
            for block in catalogue.blocks
            if block["type"] != "offers_preview"
        ] + [offers(2)]
        catalogue.data_limits = {
            "maxPickedIds": 24,
            "maxFilterIds": 5,
            "maxQueryLimit": 24,
            "maxQueriesPerDocument": 12,
        }
        catalogue.hash = "catalogue-hash-2"
        clear_catalogue_cache()

    catalogue.blocks.append(offers(1))
    return move


@pytest.fixture
def published(admin, catalogue, upgraded_catalogue, features_on):
    """A home page published at version 1 of its block."""
    draft = document(
        ("a", section("offers_preview@1", {"title": "Προσφορές", "limit": 4}))
    )
    services.save_draft(
        admin, key=HOME, draft=draft, i18n={}, expected_revision=0
    )
    services.publish(admin, keys=[HOME])
    upgraded_catalogue()
    return BuilderDocument.objects.get(key=HOME)


@pytest.fixture
def run(tenant):
    def go(*args):
        out = StringIO()
        with mock.patch(
            "django_tenants.utils.tenant_context",
            lambda _tenant: contextlib.nullcontext(),
        ):
            call_command(
                "persist_block_upgrades",
                "--tenant",
                tenant.schema_name,
                *args,
                stdout=out,
            )
        return out.getvalue()

    return go


def test_the_draft_and_the_version_move_and_keep_their_identity(published, run):
    version = published.versions.get()
    revision = published.draft_revision

    report = run()

    published.refresh_from_db()
    after = BuilderVersion.objects.get(pk=version.pk)
    expected = {
        "type": "offers_preview@2",
        "settings": {"title": "Προσφορές", "offers": {"limit": 4}},
    }
    assert published.draft["sections"]["a"] == expected
    assert after.data["sections"]["a"] == expected
    assert after.number == version.number
    assert published.draft_revision == revision
    assert "upgraded 1 draft(s), 1 version(s)" in report


def test_a_version_states_what_it_now_holds(published, run):
    version = published.versions.get()

    run()

    after = BuilderVersion.objects.get(pk=version.pk)
    assert after.block_types == ["offers_preview@2"]
    assert after.content_hash != version.content_hash
    assert after.catalogue_hash == "catalogue-hash-2"


def test_a_store_is_not_told_it_has_unpublished_changes(published, run):
    before = published.published_revision

    run()

    published.refresh_from_db()
    assert published.published_revision == before == published.draft_revision


def test_a_published_page_gets_the_dependency_rows_of_its_data(published, run):
    assert not BuilderReference.objects.filter(kind="queryPromotion").exists()

    run()

    assert BuilderReference.objects.filter(
        kind="queryPromotion", document=HOME
    ).exists()


def test_a_translation_moves_with_its_setting(
    admin, catalogue, upgraded_catalogue, features_on, run
):
    """The draft and the version both, and a language reads what it read:
    a setting renamed by an upgrade keeps its translation."""
    draft = document(
        ("a", section("offers_preview@1", {"title": "Προσφορές", "limit": 4}))
    )
    services.save_draft(
        admin,
        key=HOME,
        draft=draft,
        i18n={"en": {"a": {"/title": "Offers"}}},
        expected_revision=0,
    )
    services.publish(admin, keys=[HOME])
    upgraded_catalogue()
    current = offers(2)
    catalogue.blocks = [
        block for block in catalogue.blocks if block["type"] != "offers_preview"
    ] + [
        {
            **current,
            "version": 3,
            "accepts": [3],
            "translatable": ["/heading"],
            "schema": _schema(
                heading=dict(TEXT),
                offers=current["schema"]["properties"]["offers"],
            ),
        }
    ]
    catalogue.hash = "catalogue-hash-3"
    clear_catalogue_cache()

    run()

    stored = BuilderDocument.objects.get(key=HOME)
    version = stored.versions.get()
    moved = {"en": {"a": {"/heading": "Offers"}}}
    assert stored.draft_i18n == moved
    assert version.i18n == moved
    assert version.data["sections"]["a"]["settings"]["heading"] == "Προσφορές"
    assert services.check_publish(admin, keys=[HOME]).problems[0].code == (
        "nothing_to_publish"
    )


def test_the_upgraded_draft_is_the_upgraded_release(admin, published, run):
    run()

    check = services.check_publish(admin, keys=[HOME])

    assert [issue.code for issue in check.problems] == ["nothing_to_publish"]


def test_an_edit_of_the_upgraded_draft_publishes(admin, published, run):
    run()
    published.refresh_from_db()
    draft = published.draft
    draft["sections"]["a"]["settings"]["title"] = "Νέες προσφορές"
    services.save_draft(
        admin,
        key=HOME,
        draft=draft,
        i18n={},
        expected_revision=published.draft_revision,
    )

    assert services.check_publish(admin, keys=[HOME]).ok


def test_a_dry_run_writes_nothing(published, run):
    version = published.versions.get()

    report = run("--dry-run")

    published.refresh_from_db()
    after = BuilderVersion.objects.get(pk=version.pk)
    assert published.draft["sections"]["a"]["type"] == "offers_preview@1"
    assert after.content_hash == version.content_hash
    assert "would upgrade 1 draft(s), 1 version(s)" in report


def test_a_second_run_finds_nothing(published, run):
    run()
    version = published.versions.get()

    report = run()

    assert "upgraded 0 draft(s), 0 version(s): {}" in report
    after = BuilderVersion.objects.get(pk=version.pk)
    assert after.content_hash == version.content_hash


def test_a_feature_switched_off_does_not_stop_the_store(
    published, catalogue, run
):
    """Its block may still be on a page: the next publish refuses that, and
    the page must not stay on an old version until then."""
    gated = next(b for b in catalogue.blocks if b["type"] == "offers_preview")
    gated["feature"] = "newsletter"
    clear_catalogue_cache()

    with mock.patch("core.store_settings.store_setting", return_value=False):
        run()

    version = published.versions.get()
    assert version.data["sections"]["a"]["type"] == "offers_preview@2"


def test_one_document_that_fails_stops_the_store(published, run):
    """The version still upgrades cleanly; the draft does not, so neither is
    written."""
    version = published.versions.get()
    bad = document(("a", section("offers_preview@1", {"nonsense": True})))
    BuilderDocument.objects.filter(pk=published.pk).update(draft=bad)

    with pytest.raises(CommandError, match=rf"{HOME} draft"):
        run()

    after = BuilderVersion.objects.get(pk=version.pk)
    assert after.content_hash == version.content_hash
    assert after.data["sections"]["a"]["type"] == "offers_preview@1"
