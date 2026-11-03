"""Keep the files this repository copies from its sibling services in step.

Two services own things this repository must agree with, and a copy of what
they own can drift silently.

The storefront owns the page builder's blocks, so three things it produces
are COPIED here:

* ``catalogue``: ``test/fixtures/builder/catalogue.json`` -> ``tests/
  fixtures/builder/catalogue.json``, a snapshot of its block catalogue for the
  default schema (what the seeds are validated against, in CI, without a
  storefront);
* ``seeds``: ``test/fixtures/builder/seeds/`` -> ``builder/seeds/``, the
  default documents a new store starts with (``builder.seeding``). Unlike the
  others this one is application data, not a test input.
* ``data-cases``: ``test/fixtures/builder/data-cases.json`` -> ``tests/
  fixtures/builder/data-cases.json``, the cases both repositories run for the
  typed data props (``builder.data_sources``): a declaration, a value, whether
  it is valid, the issues, the references and the query dependencies it
  yields.
* ``placement``: ``test/fixtures/builder/placement-cases.json`` -> ``tests/
  fixtures/builder/placement-cases.json``, the cases both the editor's
  placement filter (``shared/builder/placement.ts``) and ``builder.validation.
  _placement_allowed`` are run over (``tests/unit/builder/test_placement_
  parity.py``), so a change to one rule that the other lacks fails a suite.

The media service (grooveshop-media-stream) owns what it will serve as a
source image, so one thing it publishes is COPIED here:

* ``image-limits``: ``contract/image-limits.json`` -> ``core/data/
  image-limits.json``, the per-format byte caps and the pixel limit
  ``core.uploads.validate_servable_image`` holds every upload to, so the API
  refuses at upload what the media service would show as a placeholder.

Each copy is pinned: ``tests/fixtures/upstream-sources.lock.json`` names, per
repository, the commit and, per source, the git id of what it held there (the
TREE id of a directory, the BLOB id of a file).

* ``tests/unit/core/test_upstream_sources_pin.py`` recomputes those ids from
  the copies (no network) and fails when one is not the pinned one: a copy
  cannot be edited by hand or half refreshed.
* ``--check`` (the weekly workflow, ``upstream-sources.yml``) asks GitHub what
  each repository's ``main`` holds and fails when it is not the pinned one: a
  stale copy is noticed without blocking any unrelated pull request.
* with no flag this script refreshes every copy and the pin from ``main``. It
  is the only thing that writes either.

A git id is a hash of the content, so it moves when, and only when, the
content changes. Stdlib only: this runs on a bare GitHub runner.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

STOREFRONT = "vasilistotskas/grooveshop-storefront-ui-node-nuxt"
MEDIA_STREAM = "vasilistotskas/grooveshop-media-stream"
BRANCH = "main"

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "tests/fixtures/upstream-sources.lock.json"

Fetch = Callable[[str], dict]


@dataclass(frozen=True, slots=True)
class Source:
    """One thing copied from a sibling service."""

    name: str
    #: The ``owner/name`` of the repository that owns it.
    repository: str
    #: Where it is in that repository.
    upstream: str
    #: Where the copy lives here.
    target: str
    #: ``tree`` (a flat directory of files) or ``blob`` (one file).
    kind: str


SOURCES: tuple[Source, ...] = (
    Source(
        "catalogue",
        STOREFRONT,
        "test/fixtures/builder/catalogue.json",
        "tests/fixtures/builder/catalogue.json",
        "blob",
    ),
    Source(
        "seeds",
        STOREFRONT,
        "test/fixtures/builder/seeds",
        "builder/seeds",
        "tree",
    ),
    Source(
        "placement",
        STOREFRONT,
        "test/fixtures/builder/placement-cases.json",
        "tests/fixtures/builder/placement-cases.json",
        "blob",
    ),
    Source(
        "data-cases",
        STOREFRONT,
        "test/fixtures/builder/data-cases.json",
        "tests/fixtures/builder/data-cases.json",
        "blob",
    ),
    Source(
        "image-limits",
        MEDIA_STREAM,
        "contract/image-limits.json",
        "core/data/image-limits.json",
        "blob",
    ),
)


def blob_sha(content: bytes) -> str:
    """The git blob id of *content*."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def tree_sha(files: Mapping[str, bytes]) -> str:
    """The git tree id of a flat directory of regular files: what ``git
    rev-parse HEAD:<dir>`` and GitHub's tree API call it."""
    body = b"".join(
        b"100644 " + name.encode() + b"\0" + bytes.fromhex(blob_sha(content))
        for name, content in sorted(
            files.items(), key=lambda item: item[0].encode()
        )
    )
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def local_files(source: Source) -> dict[str, bytes]:
    directory = ROOT / source.target
    return {
        path.name: path.read_bytes()
        for path in directory.iterdir()
        if path.is_file()
    }


def local_sha(source: Source) -> str:
    """The git id of the copy of *source*."""
    if source.kind == "blob":
        return blob_sha((ROOT / source.target).read_bytes())
    return tree_sha(local_files(source))


def read_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def github(path: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com{path}", headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _find(
    root_tree: str, source: Source, fetch: Fetch, commit: str
) -> tuple[str, dict[str, str]]:
    """The git id of *source* under *root_tree* and, for a directory, its
    ``{file name: blob id}``."""
    sha = root_tree
    parts = source.upstream.split("/")
    for position, part in enumerate(parts):
        listing = fetch(f"/repos/{source.repository}/git/trees/{sha}")
        wanted = (
            "blob"
            if position == len(parts) - 1 and source.kind == "blob"
            else "tree"
        )
        entry = next(
            (
                item
                for item in listing["tree"]
                if item["path"] == part and item["type"] == wanted
            ),
            None,
        )
        if entry is None:
            raise SystemExit(
                f"{source.repository}@{commit} has no {source.upstream}"
            )
        sha = entry["sha"]
    if source.kind == "blob":
        return sha, {}
    files = {
        item["path"]: item["sha"]
        for item in fetch(f"/repos/{source.repository}/git/trees/{sha}")["tree"]
        if item["type"] == "blob"
    }
    return sha, files


def latest(
    fetch: Fetch = github,
) -> tuple[dict[str, str], dict[str, tuple[str, dict[str, str]]]]:
    """``({repository: commit}, {source name: (git id, {file: blob id})})`` at
    each repository's ``main``. Every id is read from the commit itself, not
    from ``main`` again: ``main`` may move between two calls and the ids of one
    repository must describe one commit."""
    commits: dict[str, str] = {}
    roots: dict[str, str] = {}
    for repository in sorted({source.repository for source in SOURCES}):
        commit = fetch(f"/repos/{repository}/commits/{BRANCH}")
        commits[repository] = commit["sha"]
        roots[repository] = commit["commit"]["tree"]["sha"]
    return commits, {
        source.name: _find(
            roots[source.repository],
            source,
            fetch,
            commits[source.repository],
        )
        for source in SOURCES
    }


def download(blob: str, repository: str, fetch: Fetch = github) -> bytes:
    payload = fetch(f"/repos/{repository}/git/blobs/{blob}")
    return base64.b64decode(payload["content"])


def check(fetch: Fetch = github) -> int:
    pinned = read_lock()["sources"]
    commits, upstream = latest(fetch)
    problems = []
    for source in SOURCES:
        want = pinned[source.name]["sha"]
        commit = commits[source.repository]
        if (own := local_sha(source)) != want:
            problems.append(
                f"{source.name}: the copy is not the pinned one ({own[:7]} "
                f"vs pin {want[:7]}): it was edited by hand."
            )
        elif (now := upstream[source.name][0]) != want:
            problems.append(
                f"{source.name}: {source.repository}'s {source.upstream} "
                f"moved: pinned {want[:7]}, {BRANCH} at {commit[:7]} is "
                f"{now[:7]}."
            )
    if problems:
        print("\n".join(problems))
        print(
            "Run `python .github/scripts/sync_upstream_sources.py`, replay "
            "the tests of what moved (builder, uploads) and commit the "
            "copies: a failing test then is a contract change to agree on, "
            "not to paper over."
        )
        return 1
    print(
        "Every copy is its owner's: "
        + ", ".join(f"{repo}@{sha[:7]}" for repo, sha in commits.items())
        + "."
    )
    return 0


def sync(fetch: Fetch = github) -> int:
    commits, upstream = latest(fetch)
    sources: dict[str, dict[str, str]] = {}
    for source in SOURCES:
        sha, files = upstream[source.name]
        target = ROOT / source.target
        if source.kind == "blob":
            content = download(sha, source.repository, fetch)
            if blob_sha(content) != sha:
                raise SystemExit(f"{source.name} did not download intact")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        else:
            contents = {
                name: download(blob, source.repository, fetch)
                for name, blob in files.items()
            }
            for name, content in contents.items():
                if blob_sha(content) != files[name]:
                    raise SystemExit(
                        f"{source.name}/{name} did not download intact"
                    )
            if tree_sha(contents) != sha:
                raise SystemExit(
                    f"the downloaded {source.name} files do not make the pinned tree"
                )
            target.mkdir(parents=True, exist_ok=True)
            for stale in set(local_files(source)) - set(contents):
                (target / stale).unlink()
            for name, content in contents.items():
                (target / name).write_bytes(content)
        sources[source.name] = {
            "repository": source.repository,
            "path": source.upstream,
            "kind": source.kind,
            "sha": sha,
        }
    # Bytes, so the file is the same under every platform's line endings.
    LOCK.write_bytes(
        (
            json.dumps(
                {"commits": commits, "sources": sources},
                indent=2,
            )
            + "\n"
        ).encode()
    )
    print(
        f"Copied {', '.join(s.name for s in SOURCES)} from "
        + ", ".join(f"{repo}@{sha[:7]}" for repo, sha in commits.items())
        + "."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if a copy or an owner's main is not the pinned one.",
    )
    args = parser.parse_args(argv)
    return check() if args.check else sync()


if __name__ == "__main__":
    sys.exit(main())
