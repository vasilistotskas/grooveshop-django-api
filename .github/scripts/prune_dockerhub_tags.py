"""Prune old image tags from a Docker Hub repository.

Every release pushes a tag and nothing ever removed one, so
``gro0ve/grooveshop-django-api`` reached 718 tags going back ~1160 days
— roughly 100 GB at ~240 MB a tag. Docker Hub's own Lifecycle Policies
would do this natively, but they are a Pro/Team/Business feature, so
this drives the first-party API directly rather than pulling in a
third-party action.

Deleting a tag is not recoverable and rebuilding an old one is not
guaranteed to reproduce (dependencies drift), so the policy errs
generous: a tag survives if it is among the newest ``keep``, OR younger
than ``min_age_days``, OR named ``latest``. Both conditions apply, so a
quiet release period cannot age out a tag that is still in service.

Stdlib only — this runs on a bare GitHub runner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

HUB = "https://hub.docker.com/v2"
LATEST = "latest"


def _request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict | None = None,
) -> tuple[int, dict | None]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"JWT {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def login(username: str, password: str) -> str:
    """Exchange credentials for a JWT. A PAT needs Read & Delete scope."""
    status, body = _request(
        f"{HUB}/users/login/",
        method="POST",
        payload={"username": username, "password": password},
    )
    if status != 200 or not body or not body.get("token"):
        raise SystemExit(f"Docker Hub login failed (HTTP {status})")
    return body["token"]


def fetch_tags(repo: str, token: str) -> list[dict]:
    """Every tag in the repo. The API caps page_size at 100 and its
    ``ordering`` parameter is unreliable, so sorting happens here."""
    tags: list[dict] = []
    url: str | None = f"{HUB}/repositories/{repo}/tags/?page_size=100"
    while url:
        status, body = _request(url, token=token)
        if status != 200 or body is None:
            raise SystemExit(f"Listing tags failed (HTTP {status})")
        tags.extend(body.get("results", []))
        url = body.get("next")
    return tags


def select_doomed(
    tags: list[dict], keep: int, min_age_days: int, now: datetime | None = None
) -> list[dict]:
    """The tags safe to delete, newest-first ordering applied first.

    Kept separate from any I/O so it can be exercised against real
    listings without touching the delete endpoint.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=min_age_days)

    def when(tag: dict) -> datetime:
        stamp = tag.get("last_updated") or "1970-01-01T00:00:00Z"
        return datetime.fromisoformat(stamp)

    ordered = sorted(tags, key=when, reverse=True)
    return [
        tag
        for tag in ordered[keep:]
        if tag.get("name") != LATEST and when(tag) < cutoff
    ]


def delete(repo: str, token: str, name: str) -> bool:
    status, _ = _request(
        f"{HUB}/repositories/{repo}/tags/{name}/", method="DELETE", token=token
    )
    # 204 deleted, 404 already gone — both are the outcome we want.
    if status in (204, 404):
        return True
    print(f"::warning::DELETE {name} returned {status}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--keep", type=int, default=100)
    parser.add_argument("--min-age-days", type=int, default=90)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete; default is a dry run",
    )
    args = parser.parse_args()

    username = os.environ.get("DOCKER_USERNAME", "")
    password = os.environ.get("DOCKER_PASSWORD", "")
    if not username or not password:
        raise SystemExit("DOCKER_USERNAME and DOCKER_PASSWORD are required")

    token = login(username, password)
    tags = fetch_tags(args.repo, token)
    doomed = select_doomed(tags, args.keep, args.min_age_days)

    freed = sum(t.get("full_size") or 0 for t in doomed) / 1e9
    print(
        f"{args.repo}: {len(tags)} tags, "
        f"{len(doomed)} prunable (~{freed:.1f} GB), "
        f"keeping {len(tags) - len(doomed)}"
    )
    for tag in doomed[:20]:
        print(f"  - {tag['name']}  ({tag.get('last_updated')})")
    if len(doomed) > 20:
        print(f"  ... and {len(doomed) - 20} more")

    if not doomed:
        return 0
    if not args.apply:
        print(f"::notice::Dry run — nothing deleted ({len(doomed)} matched).")
        return 0

    failed = sum(not delete(args.repo, token, t["name"]) for t in doomed)
    print(f"::notice::Deleted {len(doomed) - failed} tag(s); {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
