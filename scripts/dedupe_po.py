"""One-shot deduper for .po files with accidental duplicate msgids.

When two entries share the same (msgid, msgid_plural, msgctxt) key,
the surviving entry is the one with a non-empty msgstr; source
references (`#:` occurrences) from both are merged so we don't lose
provenance.

Usage:
    uv run python scripts/dedupe_po.py <path/to/django.po>

Idempotent — running against an already-clean file is a no-op.
"""

from __future__ import annotations

import sys
from collections import OrderedDict

import polib


def dedupe(path: str) -> tuple[int, int]:
    po = polib.pofile(path)
    keep: OrderedDict[tuple, polib.POEntry] = OrderedDict()
    dropped = 0

    for entry in po:
        key = (entry.msgid, entry.msgid_plural, entry.msgctxt)
        if key not in keep:
            keep[key] = entry
            continue

        existing = keep[key]
        incoming = entry
        # Pick the entry with a non-empty translation. For plurals, a
        # "non-empty" msgstr means at least one plural form is filled in.
        existing_has_translation = bool(existing.msgstr) or any(
            (existing.msgstr_plural or {}).values()
        )
        incoming_has_translation = bool(incoming.msgstr) or any(
            (incoming.msgstr_plural or {}).values()
        )

        if not existing_has_translation and incoming_has_translation:
            incoming.occurrences = _merge_occurrences(
                existing.occurrences, incoming.occurrences
            )
            keep[key] = incoming
        else:
            existing.occurrences = _merge_occurrences(
                existing.occurrences, incoming.occurrences
            )
        dropped += 1

    new_po = polib.POFile()
    new_po.metadata = po.metadata
    new_po.metadata_is_fuzzy = po.metadata_is_fuzzy
    for entry in keep.values():
        new_po.append(entry)
    new_po.save(path)

    return dropped, len(keep)


def _merge_occurrences(a, b):
    # list(dict.fromkeys(...)) preserves order and removes duplicates.
    return list(dict.fromkeys(list(a) + list(b)))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: dedupe_po.py <path/to/django.po>\n")
        return 2

    path = argv[1]
    dropped, kept = dedupe(path)
    print(f"Deduped {path}: dropped {dropped} duplicates, kept {kept} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
