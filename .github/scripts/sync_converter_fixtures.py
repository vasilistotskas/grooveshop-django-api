"""Keep the storefront's converter fixtures, copied into this repository, in
step with the storefront.

``tests/fixtures/builder/legacy/`` is a COPY of the storefront's
``test/fixtures/builder/legacy/`` (the golden ``{request, response}`` pairs
of its legacy converter; ``tests/integration/builder/
test_converter_fixtures.py`` replays them). A copy can drift silently, so it
is pinned: ``tests/fixtures/builder/legacy.source.json`` names the storefront
commit and the git TREE id of that directory at that commit.

* ``tests/unit/builder/test_converter_fixture_pin.py`` recomputes the tree id
  of the copy (no network) and fails when it is not the pinned one: the copy
  cannot be edited by hand or half refreshed.
* ``--check`` (the weekly workflow, ``converter-fixtures.yml``) asks GitHub
  for the tree id of the storefront's ``main`` and fails when it is not the
  pinned one: a stale copy is noticed without blocking any unrelated pull
  request.
* with no flag this script refreshes the copy and the pin from ``main``. It is
  the only thing that writes either.

A tree id is a hash of the directory's content, so it moves when, and only
when, a fixture changes. Stdlib only: this runs on a bare GitHub runner.
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
from pathlib import Path

REPOSITORY = "vasilistotskas/grooveshop-storefront-ui-node-nuxt"
SOURCE_PATH = "test/fixtures/builder/legacy"
BRANCH = "main"

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "tests/fixtures/builder/legacy"
PIN = ROOT / "tests/fixtures/builder/legacy.source.json"

Fetch = Callable[[str], dict]


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


def local_files() -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in DIRECTORY.iterdir()
        if path.is_file()
    }


def local_tree() -> str:
    return tree_sha(local_files())


def read_pin() -> dict[str, str]:
    return json.loads(PIN.read_text(encoding="utf-8"))


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


def latest(fetch: Fetch = github) -> tuple[str, str, dict[str, str]]:
    """``(commit, tree id of the fixtures, {file name: blob id})`` at the
    storefront's ``main``. The tree is read from the commit itself, not from
    ``main`` again: ``main`` may move between two calls and the pair must
    describe one commit."""
    commit = fetch(f"/repos/{REPOSITORY}/commits/{BRANCH}")
    tree = commit["commit"]["tree"]["sha"]
    for part in SOURCE_PATH.split("/"):
        listing = fetch(f"/repos/{REPOSITORY}/git/trees/{tree}")
        entry = next(
            (
                item
                for item in listing["tree"]
                if item["path"] == part and item["type"] == "tree"
            ),
            None,
        )
        if entry is None:
            raise SystemExit(
                f"{REPOSITORY}@{commit['sha']} has no {SOURCE_PATH}/"
            )
        tree = entry["sha"]
    files = {
        item["path"]: item["sha"]
        for item in fetch(f"/repos/{REPOSITORY}/git/trees/{tree}")["tree"]
        if item["type"] == "blob"
    }
    return commit["sha"], tree, files


def download(blob: str, fetch: Fetch = github) -> bytes:
    payload = fetch(f"/repos/{REPOSITORY}/git/blobs/{blob}")
    return base64.b64decode(payload["content"])


def check(fetch: Fetch = github) -> int:
    pinned = read_pin()["tree"]
    commit, tree, _ = latest(fetch)
    own = local_tree()
    if own != pinned:
        print(
            f"The copy is not the pinned one ({own[:7]} vs pin {pinned[:7]}): "
            "it was edited by hand. Run this script to refresh it."
        )
        return 1
    if tree != pinned:
        print(
            f"The storefront's {SOURCE_PATH}/ moved: pinned {pinned[:7]}, "
            f"{BRANCH} at {commit[:7]} is {tree[:7]}. Run "
            "`python .github/scripts/sync_converter_fixtures.py`, replay "
            "tests/integration/builder/test_converter_fixtures.py and commit "
            "the copy: a failing replay is a contract change to agree on."
        )
        return 1
    print(f"The copy is the storefront's {SOURCE_PATH}/ at {commit[:7]}.")
    return 0


def sync(fetch: Fetch = github) -> int:
    commit, tree, files = latest(fetch)
    contents = {name: download(sha, fetch) for name, sha in files.items()}
    for name, content in contents.items():
        if blob_sha(content) != files[name]:
            raise SystemExit(f"{name} did not download intact")
    if tree_sha(contents) != tree:
        raise SystemExit("the downloaded files do not make the pinned tree")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    for stale in set(local_files()) - set(contents):
        (DIRECTORY / stale).unlink()
    for name, content in contents.items():
        (DIRECTORY / name).write_bytes(content)
    PIN.write_text(
        json.dumps(
            {
                "repository": REPOSITORY,
                "path": SOURCE_PATH,
                "commit": commit,
                "tree": tree,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Copied {len(contents)} fixture(s) from {REPOSITORY}@{commit[:7]}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the copy or the storefront's main is not the pinned tree.",
    )
    args = parser.parse_args(argv)
    return check() if args.check else sync()


if __name__ == "__main__":
    sys.exit(main())
