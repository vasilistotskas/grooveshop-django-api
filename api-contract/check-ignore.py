"""Expire the accepted API breaks in ignore.txt.

Every entry names the first release of a consumer that no longer needs it
(``until <service> <vX.Y.Z>``). Once the release LIVE in production for
that service is at or past it, the old shape cannot be served any more and
the entry is dead weight that could hide a later, different break on the
same route. This check fails for such an entry, and for any line that is
not in the required form, so overrides cannot accumulate.

    python3 -I check-ignore.py --ignore ignore.txt --live live.env

``live.env`` is what live-state.sh wrote. The check is conservative: with
several tags recorded for a service (a rollout in flight), the lowest one
decides.

Exit codes: 0 every entry is current, 1 an entry is malformed or expired,
2 the check could not run.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ENTRY = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) until (?P<service>[a-z]+) "
    r"(?P<tag>v\d+\.\d+\.\d+) \| (?P<reason>[^|]+) \| (?P<change>\S.*)$"
)
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
LIVE = re.compile(r'^PRODUCTION_(?P<service>[A-Z_]+)="(?P<tags>[^"]*)"$')


def version(tag: str) -> tuple[int, int, int]:
    match = TAG.match(tag)
    if not match:
        sys.exit(f"check-ignore: '{tag}' is not a release tag")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def production(live_file: Path) -> dict[str, tuple[int, int, int]]:
    """The lowest tag recorded for each service in production."""
    found: dict[str, tuple[int, int, int]] = {}
    for line in live_file.read_text(encoding="utf-8").splitlines():
        match = LIVE.match(line)
        if not match:
            continue
        tags = match["tags"].split()
        if tags:
            found[match["service"].lower()] = min(version(t) for t in tags)
    return found


def problems(
    ignore_file: Path, live: dict[str, tuple[int, int, int]]
) -> list[str]:
    found = []
    for number, line in enumerate(
        ignore_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip() or line.startswith("#"):
            continue
        entry = ENTRY.match(line)
        if not entry:
            found.append(
                f"line {number}: not '<date> until <service> <vX.Y.Z> | "
                "<reason> | <oasdiff line>'"
            )
            continue
        service = entry["service"]
        if service not in live:
            found.append(
                f"line {number}: no production release is recorded for "
                f"'{service}', so '{entry['tag']}' cannot be checked"
            )
            continue
        until = version(entry["tag"])
        if live[service] >= until:
            running = ".".join(map(str, live[service]))
            found.append(
                f"line {number}: expired. Production runs {service} "
                f"v{running}, at or past {entry['tag']}: delete the line "
                "(the check then proves it was no longer needed)"
            )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ignore", type=Path, required=True)
    parser.add_argument("--live", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.ignore, args.live):
        if not path.is_file():
            print(f"check-ignore: {path} does not exist", file=sys.stderr)
            return 2
    found = problems(args.ignore, production(args.live))
    for message in found:
        print(f"{args.ignore}: {message}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
