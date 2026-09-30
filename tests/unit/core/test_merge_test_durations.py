"""How CI's per-shard durations become ``.test_durations``.

The file decides how pytest-split balances the four CI shards, and a bad
merge fails quietly: every test it drops is weighted at the average,
which is exactly how shard 3 drifted toward its timeout. So the merge is
a plain function with no I/O, and these tests pin that it refuses an
incomplete or inconsistent set of shards instead of writing one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "scripts"
    / "merge_test_durations.py"
)
_spec = importlib.util.spec_from_file_location("merge_test_durations", _SCRIPT)
assert _spec is not None and _spec.loader is not None
merge_test_durations = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge_test_durations)
merge = merge_test_durations.merge


def test_every_shard_contributes_its_own_tests():
    merged = merge(
        {
            1: {"tests/a.py::test_one": 1.5},
            2: {"tests/b.py::test_two": 0.25},
        },
        expected=2,
    )

    assert merged == {
        "tests/a.py::test_one": 1.5,
        "tests/b.py::test_two": 0.25,
    }


def test_the_result_is_sorted_like_pytest_split_writes_it():
    merged = merge(
        {1: {"tests/z.py::test": 1.0}, 2: {"tests/a.py::test": 1.0}},
        expected=2,
    )

    assert list(merged) == ["tests/a.py::test", "tests/z.py::test"]


def test_a_missing_shard_is_refused():
    with pytest.raises(ValueError, match=r"missing shard\(s\): \[3\]"):
        merge(
            {1: {"tests/a.py::t": 1.0}, 2: {}, 4: {}},
            expected=4,
        )


def test_a_test_claimed_by_two_shards_is_refused():
    with pytest.raises(ValueError, match="recorded by two shards"):
        merge(
            {1: {"tests/a.py::t": 1.0}, 2: {"tests/a.py::t": 2.0}},
            expected=2,
        )


def _run(run_id, **overrides):
    return {
        "databaseId": run_id,
        "workflowName": "CI",
        "headBranch": "main",
        "event": "push",
        "conclusion": "success",
        **overrides,
    }


def test_the_newest_successful_main_push_run_is_picked():
    runs = [
        _run(6, workflowName="Publish Docker image"),
        _run(5, headBranch="fix/x"),
        _run(4, event="pull_request"),
        _run(3, conclusion="failure"),
        _run(2),
        _run(1),
    ]

    assert merge_test_durations.latest_successful_run(runs) == "2"


def test_no_successful_main_run_is_an_error_not_a_guess():
    with pytest.raises(LookupError):
        merge_test_durations.latest_successful_run(
            [_run(1, conclusion="failure")]
        )
