"""Rebuild ``.test_durations`` from the durations CI itself recorded.

pytest-split balances the four CI shards from ``.test_durations``. That
file used to be refreshed by running the whole suite locally, which
records a developer machine's timings, not a runner's: the 2026-09-26
refresh estimated the groups within 0.7% of each other, yet on CI they
ran from 9 to 16 minutes, and shard 3 came within a minute of its
18-minute timeout.

Each shard now stores the durations of the tests it ran, measured on the
runner under the same coverage and xdist load, and uploads them as the
``test-durations-shard-<n>`` artifact. This script downloads those
artifacts from one CI run and merges them into ``.test_durations``::

    python .github/scripts/merge_test_durations.py --run-id 123456789

Without ``--run-id`` it takes the latest successful CI run on ``main``.
Every shard must be present: a partial merge would silently drop a
quarter of the suite back to the average weight, which is what caused
the imbalance in the first place.

Stdlib only, plus the ``gh`` CLI for the download.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ARTIFACT_PREFIX = "test-durations-shard-"
WORKFLOW = "ci.yml"


def merge(shards: dict[int, dict[str, float]], expected: int) -> dict:
    """One duration per test, from exactly the shards ``1..expected``.

    Raises ``ValueError`` when a shard is missing or two shards both
    claim a test — pytest-split never runs a test in two groups, so an
    overlap means the files do not come from the same split.
    """
    missing = sorted(set(range(1, expected + 1)) - set(shards))
    if missing:
        raise ValueError(f"missing shard(s): {missing}")

    merged: dict[str, float] = {}
    for group in sorted(shards):
        for test, duration in shards[group].items():
            if test in merged:
                raise ValueError(f"{test} was recorded by two shards")
            merged[test] = duration
    return dict(sorted(merged.items()))


def _gh(*args: str) -> str:
    return subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    ).stdout


def _latest_successful_run() -> str:
    return _gh(
        "run",
        "list",
        "--workflow",
        WORKFLOW,
        "--branch",
        "main",
        "--status",
        "success",
        "--limit",
        "1",
        "--json",
        "databaseId",
        "--jq",
        ".[0].databaseId",
    ).strip()


def _download(run_id: str, into: Path) -> dict[int, dict[str, float]]:
    _gh(
        "run",
        "download",
        run_id,
        "--pattern",
        f"{ARTIFACT_PREFIX}*",
        "--dir",
        str(into),
    )
    shards: dict[int, dict[str, float]] = {}
    for directory in into.glob(f"{ARTIFACT_PREFIX}*"):
        group = int(directory.name.removeprefix(ARTIFACT_PREFIX))
        (file,) = directory.iterdir()
        shards[group] = json.loads(file.read_text(encoding="utf-8"))
    return shards


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run-id", help="CI run to take durations from")
    parser.add_argument("--shards", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path(".test_durations"))
    args = parser.parse_args()

    run_id = args.run_id or _latest_successful_run()
    with tempfile.TemporaryDirectory() as tmp:
        shards = _download(run_id, Path(tmp))
    try:
        merged = merge(shards, args.shards)
    except ValueError as exc:
        print(f"run {run_id}: {exc}", file=sys.stderr)
        return 1

    # Same layout pytest-split writes, so a refresh diffs cleanly.
    args.output.write_text(
        json.dumps(merged, sort_keys=True, indent=4), encoding="utf-8"
    )
    for group in sorted(shards):
        total = sum(shards[group].values()) / 60
        print(f"shard {group}: {len(shards[group])} tests, {total:.1f} min")
    print(f"run {run_id}: wrote {len(merged)} tests to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
