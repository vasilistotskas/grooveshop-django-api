"""``provision_meilisearch_indexes`` is what makes the live-engine suites
runnable anywhere, so its two properties are worth pinning.

Those suites query ``/api/v1/search/*``, which reads real indexes, and
every one of them assumed the developer's local engine already held
indexes from a dev run. Against the empty engine CI starts for each job,
product and blog search answered HTTP 500 (``index_not_found``) and
federated search HTTP 400 (a filter against an index whose
``filterableAttributes`` is the default ``[]``) — 23 failures reproduced
locally against a throwaway ``getmeili/meilisearch`` container.

The retry is not decoration: ``Client.create_index`` reads the index list
and then acts on it, so two xdist workers starting together can both find
an index missing and both enqueue the create. The loser's task fails with
``index_already_exists``, which ``update_meili_settings`` raises on.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import provision_meilisearch_indexes


class _Model:
    def __init__(self, side_effect=None):
        self.update_meili_settings = MagicMock(side_effect=side_effect)


def _run(models, client):
    with (
        patch("meili.models.IndexMixin.__subclasses__", return_value=models),
        patch("meili._client.client", client),
    ):
        provision_meilisearch_indexes()


def test_every_index_model_is_provisioned():
    product, blog = _Model(), _Model()

    _run([product, blog], MagicMock())

    assert product.update_meili_settings.call_count == 1
    assert blog.update_meili_settings.call_count == 1


def test_a_lost_create_race_is_retried_and_the_stale_task_flushed():
    """The loser of the create race must recover, not fail the session.

    On the retry the index exists, ``create_index`` skips it and only the
    idempotent settings task is sent. ``flush_tasks`` has to run first:
    the failed task is still on the process-wide client's list, and
    ``update_meili_settings`` awaits every task on it, so without the
    flush the retry re-awaits the same failure forever.
    """
    client = MagicMock()
    model = _Model(side_effect=[RuntimeError("index_already_exists"), None])

    _run([model], client)

    assert model.update_meili_settings.call_count == 2
    client.flush_tasks.assert_called_once_with()


def test_a_persistently_broken_engine_still_surfaces():
    """Retrying must not turn a real outage into a silent skip."""
    client = MagicMock()
    model = _Model(side_effect=RuntimeError("engine down"))

    with pytest.raises(RuntimeError, match="engine down"):
        _run([model], client)

    assert model.update_meili_settings.call_count == 3
