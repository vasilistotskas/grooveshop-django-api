"""Put real documents into the live Meilisearch engine for one test.

``@requires_meilisearch`` provisions the indexes and their settings but
leaves them empty, and the suite runs with ``MEILISEARCH["OFFLINE"]``,
so saving a model never indexes it. A live search test therefore has to
index its own documents; ``LiveSearchIndex`` does that synchronously and
removes them again afterwards.

Documents are built by the project's own code: each instance is
re-read through ``get_meilisearch_queryset()`` (the bulk-reindex
queryset, with its annotations) and serialised by ``meili_serialize()``
inside the ``id``/``pk`` envelope that ``meili.tasks.index_document_task``
wraps it in. ``meili_filter()`` is deliberately NOT consulted: a test
can index the document of an inactive product or an unpublished post,
which is exactly the stale document the search views' own filters exist
to hide.

Isolation. Every xdist worker - and any other pytest session on the
machine - shares ONE engine and, because the suite runs in the public
schema, the same unprefixed index uids (``ProductTranslation``,
``BlogPostTranslation``). Per-worker index uids are not an option: the
uid comes from ``connection.schema_name``, and faking a schema would
change the code under test. Two things keep tests apart instead:

1. Document ids. A document's id is its row's primary key, and every
   worker has its own database whose sequences all start at 1, so two
   workers would write the SAME document id and overwrite (and, on
   teardown, delete) each other's documents. Entering the context moves
   the indexed tables' sequences to a random point in a 10**15-wide
   range above 10**12, far from each other and from the few million
   ids a developer's seeded engine may hold. ``setval`` is not
   transactional, so the jump survives the test's rollback - harmless,
   the next test jumps again.
2. Query text. Categories, prices and every other field still collide
   across workers, so tests search for a ``unique_marker()`` token they
   put in the indexed text; only their own documents can match it.

Teardown enqueues the deletions without waiting for them. Nothing
depends on their completion - no later test can match these markers -
and on a developer engine holding millions of documents each deletion
takes seconds.
"""

from __future__ import annotations

import secrets
import string
import warnings
from collections import defaultdict
from typing import TYPE_CHECKING, Self

from django.db import connection

from meili._client import client as meili_client
from meili.models import IndexMixin
from meili.tasks import _get_document_pk

if TYPE_CHECKING:
    from types import TracebackType

# The SDK waits 5 s by default. On a developer engine whose public
# ``ProductTranslation`` index holds millions of seeded documents, one
# addition takes 10-25 s - a timeout there would say nothing about search.
TASK_TIMEOUT_MS = 180_000

_ID_FLOOR = 10**12
_ID_SPAN = 10**15

# Greek letters whose canonical Greeklish fold is one fixed Latin letter.
# Alternating consonant/vowel keeps every digraph the transliteration
# treats specially (ου, αι, μπ, ντ, γκ, ...) out of the generated words.
_GREEK_CONSONANTS = {
    "δ": "d",
    "ζ": "z",
    "κ": "k",
    "λ": "l",
    "ρ": "r",
    "σ": "s",
    "τ": "t",
    "φ": "f",
}
_GREEK_VOWELS = {"α": "a", "ε": "e", "ο": "o"}


def unique_marker(length: int = 12) -> str:
    """A random lowercase Latin word no other document contains."""
    return "".join(
        secrets.choice(string.ascii_lowercase) for _ in range(length)
    )


def unique_greek_word(syllables: int = 6) -> tuple[str, str]:
    """A random Greek word and its canonical Greeklish spelling.

    The Latin spelling is derived here, from a fixed letter table, not
    by ``search.transliteration`` - it is the expectation that code is
    tested against.
    """
    greek, latin = [], []
    for _ in range(syllables):
        consonant = secrets.choice(list(_GREEK_CONSONANTS))
        vowel = secrets.choice(list(_GREEK_VOWELS))
        greek.append(consonant + vowel)
        latin.append(_GREEK_CONSONANTS[consonant] + _GREEK_VOWELS[vowel])
    return "".join(greek), "".join(latin)


def build_document(instance: IndexMixin) -> dict:
    """The document production indexes for ``instance``."""
    fresh = type(instance).get_meilisearch_queryset().get(pk=instance.pk)
    return {
        **fresh.meili_serialize(),
        "id": _get_document_pk(fresh),
        "pk": fresh._meta.pk.value_to_string(fresh),
    }


class LiveSearchIndex:
    """Index model instances into the live engine for one test.

    Enter it BEFORE creating the rows to index (see the module
    docstring); ``add`` refuses rows whose ids predate the context.
    """

    def __init__(self) -> None:
        self._first_id = 0
        self._added: dict[str, list[str]] = defaultdict(list)

    def __enter__(self) -> Self:
        self._first_id = _ID_FLOOR + secrets.randbelow(_ID_SPAN)
        with connection.cursor() as cursor:
            for model in IndexMixin.__subclasses__():
                cursor.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, %s), %s, false)",
                    [
                        model._meta.db_table,
                        model._meta.pk.column,
                        self._first_id,
                    ],
                )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # The SDK deprecates deleting by ids in favour of a filter, but
        # ``id`` is not filterable and the filterable ``master_id``
        # collides across workers - a filter would delete their documents.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            for index_uid, document_ids in self._added.items():
                meili_client.get_index(index_uid).delete_documents(document_ids)
        self._added.clear()

    def add(self, *instances: IndexMixin) -> None:
        """Index ``instances`` and wait until the engine serves them."""
        batches: dict[str, list[dict]] = defaultdict(list)
        for instance in instances:
            if instance.pk < self._first_id:
                raise AssertionError(
                    f"{instance!r} was created before LiveSearchIndex was "
                    "entered, so its document id can collide with another "
                    "worker's. Create the rows inside the context."
                )
            batches[type(instance).get_meili_index_name()].append(
                build_document(instance)
            )

        task_uids = []
        for index_uid, documents in batches.items():
            task = meili_client.get_index(index_uid).add_documents(documents)
            # Recorded before waiting, so a failed wait still cleans up.
            self._added[index_uid].extend(doc["id"] for doc in documents)
            task_uids.append(task.task_uid)

        for task_uid in task_uids:
            finished = meili_client.wait_for_task(
                task_uid, timeout_in_ms=TASK_TIMEOUT_MS, interval_in_ms=100
            )
            if finished.status != "succeeded":
                raise AssertionError(
                    f"Meilisearch task {task_uid} {finished.status}: "
                    f"{finished.error}"
                )
