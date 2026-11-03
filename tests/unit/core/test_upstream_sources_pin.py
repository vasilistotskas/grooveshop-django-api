"""What is copied from the storefront and the media service is exactly what
its pin says.

``tests/fixtures/upstream-sources.lock.json`` pins each copy to its owner's
commit by git id (the tree id of a directory, the blob id of a file);
``.github/scripts/sync_upstream_sources.py`` is the only thing that writes
either, and a weekly workflow asks whether an owner has moved since.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "scripts"
    / "sync_upstream_sources.py"
)
_spec = importlib.util.spec_from_file_location("sync_sources", _SCRIPT)
assert _spec is not None and _spec.loader is not None
sync = importlib.util.module_from_spec(_spec)
# A dataclass looks its module up by name while it is being defined.
sys.modules[_spec.name] = sync
_spec.loader.exec_module(sync)

COMMIT = "c" * 40
MEDIA_COMMIT = "d" * 40


class TestTheCommittedCopies:
    def test_every_copy_is_the_pinned_one(self):
        lock = sync.read_lock()

        assert set(lock["commits"]) == {s.repository for s in sync.SOURCES}
        assert all(len(commit) == 40 for commit in lock["commits"].values())
        assert set(lock["sources"]) == {s.name for s in sync.SOURCES}
        for source in sync.SOURCES:
            pinned = lock["sources"][source.name]
            assert pinned["repository"] == source.repository
            assert pinned["path"] == source.upstream
            assert pinned["kind"] == source.kind
            assert sync.local_sha(source) == pinned["sha"], (
                f"{source.target} is not the pinned copy: do not edit it "
                "by hand, run "
                "`python .github/scripts/sync_upstream_sources.py`"
            )

    def test_the_seeds_copy_is_a_directory_of_json_documents(self):
        seeds = next(s for s in sync.SOURCES if s.name == "seeds")
        names = sync.local_files(seeds)

        assert "home.json" in names
        assert all(name.endswith(".json") for name in names)


class TestGitHashes:
    def test_the_empty_tree_and_a_known_blob(self):
        # The two ids every git repository knows.
        assert sync.tree_sha({}) == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        assert (
            sync.blob_sha(b"hello\n")
            == "ce013625030ba8dba906f756967f9e9ca394464a"
        )

    def test_a_tree_id_is_the_one_git_computes(self, tmp_path):
        files = {
            "b.json": b'{"b": 1}\n',
            "a.json": b'{"a": "\xce\xb1"}\n',
            # Uppercase sorts before lowercase, bytewise, in a git tree (and a
            # name differing only by case would collide on Windows).
            "Z.json": b"{}\n",
        }
        for name, content in files.items():
            (tmp_path / "dir").mkdir(exist_ok=True)
            (tmp_path / "dir" / name).write_bytes(content)

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-c", "core.autocrlf=false", *args],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q")
        git("add", "dir")

        root = git("write-tree")

        assert sync.tree_sha(files) == git("rev-parse", f"{root}:dir")

    def test_order_does_not_change_the_id(self):
        forward = {"a": b"1", "b": b"2"}
        backward = {"b": b"2", "a": b"1"}

        assert sync.tree_sha(forward) == sync.tree_sha(backward)

    def test_a_changed_byte_changes_the_id(self):
        assert sync.tree_sha({"a": b"1"}) != sync.tree_sha({"a": b"2"})


class FakeGitHub:
    """The GitHub API reads the script makes, over two in-memory
    repositories: the storefront's, holding two files (``catalogue``,
    ``placement``) and one directory (``seeds``), and the media service's,
    holding the ``image-limits`` file, all at the sources' upstream paths."""

    def __init__(
        self,
        *,
        seeds: dict[str, bytes],
        catalogue: bytes = b'{"hash": "h"}\n',
        placement: bytes = b"[]\n",
        data_cases: bytes = b"[]\n",
        limits: bytes = b'{"maxFileBytes": {}}\n',
    ) -> None:
        self.directories = {"seeds": seeds}
        self.catalogue = catalogue
        self.placement = placement
        self.data_cases = data_cases
        self.limits = limits
        # Every tree this repository has: sha -> entries.
        self.trees: dict[str, list[dict]] = {}
        self.blobs: dict[str, bytes] = {}
        self.root = self._build()
        self.media_root = self._build_media()

    def _blob(self, content: bytes) -> str:
        sha = sync.blob_sha(content)
        self.blobs[sha] = content
        return sha

    def _directory(self, files: dict[str, bytes]) -> str:
        sha = sync.tree_sha(files)
        self.trees[sha] = [
            {"path": n, "type": "blob", "sha": self._blob(c)}
            for n, c in files.items()
        ]
        return sha

    def _tree(self, entries: list[dict]) -> str:
        # Not a git-exact id for a mixed tree: nothing checks one.
        sha = f"tree{len(self.trees):036d}"
        self.trees[sha] = entries
        return sha

    def _build(self) -> str:
        by_name = {s.name: s for s in sync.SOURCES}
        # test/fixtures/builder/{catalogue.json,seeds}
        builder = self._tree(
            [
                {
                    "path": "catalogue.json",
                    "type": "blob",
                    "sha": self._blob(self.catalogue),
                },
                {
                    "path": "placement-cases.json",
                    "type": "blob",
                    "sha": self._blob(self.placement),
                },
                {
                    "path": "data-cases.json",
                    "type": "blob",
                    "sha": self._blob(self.data_cases),
                },
                {
                    "path": "seeds",
                    "type": "tree",
                    "sha": self._directory(self.directories["seeds"]),
                },
            ]
        )
        assert all(
            s.upstream.startswith("test/fixtures/builder/")
            for s in by_name.values()
            if s.repository == sync.STOREFRONT
        )
        fixtures = self._tree(
            [{"path": "builder", "type": "tree", "sha": builder}]
        )
        test = self._tree(
            [{"path": "fixtures", "type": "tree", "sha": fixtures}]
        )
        return self._tree([{"path": "test", "type": "tree", "sha": test}])

    def _build_media(self) -> str:
        # contract/image-limits.json
        contract = self._tree(
            [
                {
                    "path": "image-limits.json",
                    "type": "blob",
                    "sha": self._blob(self.limits),
                }
            ]
        )
        return self._tree(
            [{"path": "contract", "type": "tree", "sha": contract}]
        )

    def __call__(self, path: str) -> dict:
        for repository, root, commit in (
            (sync.STOREFRONT, self.root, COMMIT),
            (sync.MEDIA_STREAM, self.media_root, MEDIA_COMMIT),
        ):
            repo = f"/repos/{repository}"
            if path == f"{repo}/commits/main":
                return {"sha": commit, "commit": {"tree": {"sha": root}}}
            if path.startswith(f"{repo}/git/trees/"):
                return {"tree": self.trees[path.rsplit("/", 1)[1]]}
            if path.startswith(f"{repo}/git/blobs/"):
                content = self.blobs[path.rsplit("/", 1)[1]]
                return {"content": base64.b64encode(content).decode()}
        raise AssertionError(path)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """The copies and the lock, in a temporary tree."""
    monkeypatch.setattr(sync, "ROOT", tmp_path)
    monkeypatch.setattr(sync, "LOCK", tmp_path / "upstream-sources.lock.json")
    return tmp_path


def local(workspace: Path, name: str) -> Path:
    return workspace / next(s for s in sync.SOURCES if s.name == name).target


class TestSyncing:
    def test_it_copies_every_source_and_pins_each(self, workspace):
        upstream = FakeGitHub(
            seeds={"one.json": b'{"x": 1}\n', "two.json": b"{}\n"},
            catalogue=b'{"hash": "abc"}\n',
        )
        stale = local(workspace, "seeds") / "stale.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}")

        assert sync.sync(upstream) == 0

        assert {p.name for p in local(workspace, "seeds").iterdir()} == {
            "one.json",
            "two.json",
        }
        assert (
            local(workspace, "catalogue").read_bytes() == b'{"hash": "abc"}\n'
        )
        assert local(workspace, "placement").read_bytes() == b"[]\n"
        assert local(workspace, "data-cases").read_bytes() == b"[]\n"
        assert (
            local(workspace, "image-limits").read_bytes()
            == b'{"maxFileBytes": {}}\n'
        )
        lock = json.loads(sync.LOCK.read_text(encoding="utf-8"))
        assert lock["commits"] == {
            sync.MEDIA_STREAM: MEDIA_COMMIT,
            sync.STOREFRONT: COMMIT,
        }
        for source in sync.SOURCES:
            assert lock["sources"][source.name]["sha"] == sync.local_sha(source)

    def test_a_file_that_downloads_wrong_is_refused(self, workspace):
        upstream = FakeGitHub(seeds={"one.json": b"{}\n"})

        def corrupting(path: str) -> dict:
            reply = upstream(path)
            if "/git/blobs/" in path:
                reply["content"] = base64.b64encode(b"tampered").decode()
            return reply

        with pytest.raises(SystemExit, match="intact"):
            sync.sync(corrupting)
        assert not sync.LOCK.exists()

    def test_a_storefront_without_a_source_is_refused(self, workspace):
        upstream = FakeGitHub(seeds={"one.json": b"{}\n"})
        upstream.trees[upstream.root] = []

        with pytest.raises(SystemExit, match="has no test/fixtures"):
            sync.sync(upstream)


class TestChecking:
    def synced(self, workspace, **kwargs):
        upstream = FakeGitHub(**kwargs)
        sync.sync(upstream)
        return upstream

    def test_copies_that_are_the_storefronts_pass(self, workspace, capsys):
        upstream = self.synced(workspace, seeds={"a.json": b"{}\n"})

        assert sync.check(upstream) == 0
        output = capsys.readouterr().out
        assert f"{sync.STOREFRONT}@ccccccc" in output
        assert f"{sync.MEDIA_STREAM}@ddddddd" in output

    @pytest.mark.parametrize(
        ("moved", "name"),
        [
            (
                {
                    "seeds": {"a.json": b"{}\n"},
                    "catalogue": b'{"hash": "z"}\n',
                },
                "catalogue",
            ),
            ({"seeds": {"a.json": b'{"changed": true}\n'}}, "seeds"),
            (
                {"seeds": {"a.json": b"{}\n"}, "limits": b'{"v": 2}\n'},
                "image-limits",
            ),
            (
                {"seeds": {"a.json": b"{}\n"}, "placement": b"[1]\n"},
                "placement",
            ),
            (
                {"seeds": {"a.json": b"{}\n"}, "data_cases": b"[1]\n"},
                "data-cases",
            ),
        ],
    )
    def test_a_source_that_moved_is_named(self, workspace, capsys, moved, name):
        self.synced(workspace, seeds={"a.json": b"{}\n"})

        assert sync.check(FakeGitHub(**moved)) == 1
        output = capsys.readouterr().out
        assert f"{name}: " in output
        assert "'s " in output
        assert "moved" in output

    def test_a_copy_edited_by_hand_fails_before_asking_what_moved(
        self, workspace, capsys
    ):
        upstream = self.synced(workspace, seeds={"a.json": b"{}\n"})
        local(workspace, "catalogue").write_text('{"edited": true}\n')

        assert sync.check(upstream) == 1
        assert "catalogue: the copy is not the pinned one" in (
            capsys.readouterr().out
        )
