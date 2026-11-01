"""The copy of the storefront's converter fixtures is exactly what it says.

``tests/fixtures/builder/legacy/`` is pinned to a storefront commit by its
git tree id (``legacy.source.json``); ``.github/scripts/
sync_converter_fixtures.py`` is the only thing that writes either, and a
weekly workflow asks whether the storefront has moved since.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "scripts"
    / "sync_converter_fixtures.py"
)
_spec = importlib.util.spec_from_file_location("sync_fixtures", _SCRIPT)
assert _spec is not None and _spec.loader is not None
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

COMMIT = "c" * 40


def test_the_copy_is_the_pinned_tree():
    pin = sync.read_pin()

    assert sync.local_tree() == pin["tree"], (
        "tests/fixtures/builder/legacy/ is not the pinned storefront tree: "
        "do not edit it by hand, run "
        "`python .github/scripts/sync_converter_fixtures.py`"
    )
    assert pin["repository"] == sync.REPOSITORY
    assert pin["path"] == sync.SOURCE_PATH
    assert len(pin["commit"]) == 40


def test_the_pin_describes_every_file_in_the_copy():
    names = sync.local_files()

    assert len(names) >= 40
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
    """The three GitHub API reads the script makes, over an in-memory repo."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.tree = sync.tree_sha(files)
        self.calls: list[str] = []

    def __call__(self, path: str) -> dict:
        self.calls.append(path)
        repo = f"/repos/{sync.REPOSITORY}"
        if path == f"{repo}/commits/main":
            return {"sha": COMMIT, "commit": {"tree": {"sha": "root"}}}
        nodes = {
            "root": "test",
            "t1": "fixtures",
            "t2": "builder",
            "t3": "legacy",
        }
        if path.startswith(f"{repo}/git/trees/"):
            tree = path.rsplit("/", 1)[1]
            if tree == self.tree:
                return {
                    "tree": [
                        {"path": n, "type": "blob", "sha": sync.blob_sha(c)}
                        for n, c in self.files.items()
                    ]
                }
            child = {"root": "t1", "t1": "t2", "t2": "t3", "t3": self.tree}[
                tree
            ]
            return {
                "tree": [{"path": nodes[tree], "type": "tree", "sha": child}]
            }
        if path.startswith(f"{repo}/git/blobs/"):
            blob = path.rsplit("/", 1)[1]
            content = next(
                c for c in self.files.values() if sync.blob_sha(c) == blob
            )
            return {"content": base64.b64encode(content).decode()}
        raise AssertionError(path)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    directory = tmp_path / "legacy"
    directory.mkdir()
    monkeypatch.setattr(sync, "DIRECTORY", directory)
    monkeypatch.setattr(sync, "PIN", tmp_path / "legacy.source.json")
    return directory


class TestSyncing:
    def test_it_copies_the_storefronts_files_and_pins_the_tree(self, workspace):
        upstream = FakeGitHub({"one.json": b'{"x": 1}\n', "two.json": b"{}\n"})
        (workspace / "stale.json").write_text("{}")

        assert sync.sync(upstream) == 0

        assert {p.name for p in workspace.iterdir()} == {"one.json", "two.json"}
        pin = json.loads(sync.PIN.read_text(encoding="utf-8"))
        assert pin["tree"] == upstream.tree == sync.local_tree()
        assert pin["commit"] == COMMIT

    def test_a_file_that_downloads_wrong_is_refused(self, workspace):
        upstream = FakeGitHub({"one.json": b"{}\n"})
        original = upstream.__call__

        def corrupting(path: str) -> dict:
            reply = original(path)
            if "/git/blobs/" in path:
                reply["content"] = base64.b64encode(b"tampered").decode()
            return reply

        with pytest.raises(SystemExit, match="intact"):
            sync.sync(corrupting)
        assert not sync.PIN.exists()


class TestChecking:
    def synced(self, workspace, files):
        upstream = FakeGitHub(files)
        sync.sync(upstream)
        return upstream

    def test_a_copy_that_is_the_storefronts_passes(self, workspace, capsys):
        upstream = self.synced(workspace, {"a.json": b"{}\n"})

        assert sync.check(upstream) == 0
        assert "at cccccc" in capsys.readouterr().out

    def test_a_storefront_that_moved_fails(self, workspace, capsys):
        self.synced(workspace, {"a.json": b"{}\n"})
        moved = FakeGitHub({"a.json": b'{"changed": true}\n'})

        assert sync.check(moved) == 1
        assert "moved" in capsys.readouterr().out

    def test_a_copy_edited_by_hand_fails_before_asking_what_moved(
        self, workspace, capsys
    ):
        upstream = self.synced(workspace, {"a.json": b"{}\n"})
        (workspace / "a.json").write_text('{"edited": true}\n')

        assert sync.check(upstream) == 1
        assert "edited by hand" in capsys.readouterr().out
