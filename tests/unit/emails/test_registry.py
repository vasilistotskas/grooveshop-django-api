"""The catalogue as a whole: what ``freeze`` refuses.

The production registry holds no type until the send sites move onto it,
so every case runs over probe types in a registry of its own.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from emails.registry import Registry, UnknownEmailType, registry
from emails.types import Audience, Owner
from tests.utils.emails import PROBE_TEMPLATES, add_template_root, probe


@pytest.fixture
def fresh(probe_templates) -> Registry:
    return Registry()


class TestRegister:
    def test_a_registered_type_is_found_by_its_key(self, fresh):
        notice = probe("probe.notice")

        fresh.register(notice)

        assert fresh.get("probe.notice") is notice

    def test_two_types_cannot_share_a_key(self, fresh):
        fresh.register(probe("probe.notice"))

        with pytest.raises(ImproperlyConfigured, match="registered twice"):
            fresh.register(probe("probe.notice"))

    def test_nothing_registers_after_the_freeze(self, fresh):
        fresh.freeze()

        with pytest.raises(ImproperlyConfigured, match="frozen"):
            fresh.register(probe())

    def test_an_unknown_key_is_not_found(self, fresh):
        with pytest.raises(UnknownEmailType):
            fresh.get("probe.missing")

    def test_types_are_listed_by_owner(self, fresh):
        store = fresh.register(probe("probe.store"))
        platform = fresh.register(
            probe(
                "probe.platform",
                owner=Owner.PLATFORM,
                audience=Audience.STORE_STAFF,
            )
        )

        assert fresh.all_types() == (store, platform)
        assert fresh.types_for(Owner.STORE) == (store,)
        assert fresh.types_for(Owner.PLATFORM) == (platform,)


class TestFreeze:
    def test_an_empty_catalogue_freezes(self, fresh):
        fresh.freeze()

        assert fresh.all_types() == ()

    def test_a_template_pair_that_exists_passes(self, fresh):
        fresh.register(probe())

        fresh.freeze()

    @pytest.mark.parametrize("suffix", [".html", ".txt"])
    def test_a_missing_template_is_named(self, fresh, suffix):
        fresh.register(probe(templates="emails_test/missing"))

        with pytest.raises(
            ImproperlyConfigured, match=f"emails_test/missing{suffix}"
        ):
            fresh.freeze()

    def test_a_template_no_type_names_is_refused_in_a_directory_a_type_uses(
        self, fresh, tmp_path, settings
    ):
        directory = tmp_path / "emails_owned"
        directory.mkdir()
        for name in ("used.html", "used.txt", "orphan.html", "orphan.txt"):
            (directory / name).write_text("x")
        add_template_root(settings, tmp_path)
        fresh.register(probe(templates="emails_owned/used"))

        with pytest.raises(
            ImproperlyConfigured, match="emails_owned/orphan is used by no"
        ) as raised:
            fresh.freeze()

        assert "emails_owned/used" not in str(raised.value)

    def test_a_directory_no_type_uses_is_not_scanned(
        self, fresh, tmp_path, settings
    ):
        """Mail that has not moved into the catalogue yet keeps its
        templates where they are: only directories the catalogue owns are
        checked for leftovers."""
        unowned = tmp_path / "emails_unowned"
        unowned.mkdir()
        (unowned / "legacy.html").write_text("x")
        (unowned / "legacy.txt").write_text("x")
        add_template_root(settings, tmp_path)
        fresh.register(probe(templates=PROBE_TEMPLATES))

        fresh.freeze()

    def test_a_failed_freeze_leaves_the_catalogue_open(self, fresh):
        fresh.register(probe(templates="emails_test/missing"))

        with pytest.raises(ImproperlyConfigured):
            fresh.freeze()

        fresh.register(probe("probe.other"))


def test_the_production_catalogue_is_frozen_at_startup():
    """``EmailsConfig.ready`` found the apps' types and locked the set."""
    with pytest.raises(ImproperlyConfigured, match="frozen"):
        registry.register(probe())
