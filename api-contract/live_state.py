"""What is LIVE in each environment, read from GitHub Deployments.

    python3 -I live_state.py OUTPUT_FILE ENVIRONMENT...

The cluster reconciler (grooveshop-infrastructure) records every service
release that finished rolling out as a deployment of the service's own
repository: environment ``production`` or ``staging``, ref = the release
tag, status ``success``. For each environment and each service named in
services.txt this writes one line to OUTPUT_FILE,

    PRODUCTION_STOREFRONT="v3.299.0 v3.300.1"

the newest deployment whose latest status is success (live), then any
newer deployment still rolling out (queued, in_progress, pending): pods
roll at different times, so both can be serving. A failed or errored
deployment is ignored. GitHub never makes an older deployment "inactive"
for a production environment (``auto_inactive`` only covers
non-production environments), so "inactive" is not relied on: a rollback is
simply a newer deployment of the older tag.

No record, an unreadable one, or a ref that is not a release tag fails the
run: "what is live" is unanswered, and a check must not pass on that.

Public data only. GH_TOKEN, when set, is sent for the authenticated rate
limit (GITHUB_TOKEN: 1,000 requests an hour per repository; 60 an hour per
IP without). GITHUB_API_URL points the script at a test server.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

TAG = re.compile(r"^v\d+\.\d+\.\d+$")
ROLLING = {"queued", "in_progress", "pending"}
PAGE = 30
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


class Unknown(Exception):
    """What is live cannot be said."""


def get(path: str) -> list[dict]:
    request = urllib.request.Request(
        API + path, headers={"Accept": "application/vnd.github+json"}
    )
    token = os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise Unknown(f"cannot read {API}{path}: {error}") from error


def services(path: Path) -> list[tuple[str, str]]:
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            service, repository = line.split()
            found.append((service, repository))
    return found


def live_and_rolling(repository: str, environment: str) -> list[str]:
    """The live tag, then the tags rolling out after it."""
    deployments = get(
        f"/repos/{repository}/deployments"
        f"?environment={environment}&per_page={PAGE}"
    )
    rolling: list[str] = []
    for deployment in deployments:
        statuses = get(
            f"/repos/{repository}/deployments/{deployment['id']}"
            "/statuses?per_page=1"
        )
        state = statuses[0]["state"] if statuses else "none"
        if state == "success":
            tags = [deployment["ref"], *rolling]
            for tag in tags:
                if not TAG.match(tag):
                    raise Unknown(
                        f"{repository} '{environment}' deployment ref "
                        f"'{tag}' is not a release tag"
                    )
            return tags
        if state in ROLLING:
            rolling.append(deployment["ref"])
    raise Unknown(
        f"{repository} has no successful '{environment}' deployment among "
        f"the newest {PAGE}: cannot say what is live"
    )


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    output = Path(sys.argv[1])
    lines = []
    try:
        for environment in sys.argv[2:]:
            for service, repository in services(
                Path(__file__).with_name("services.txt")
            ):
                tags = live_and_rolling(repository, environment)
                key = f"{environment}_{service}".upper()
                lines.append(f'{key}="{" ".join(tags)}"')
    except Unknown as error:
        print(f"live-state: {error}", file=sys.stderr)
        return 1
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
