"""live_state.py against a fake GitHub API.

Run: python3 -I -m unittest discover -s api-contract/tests
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

SCRIPT = Path(__file__).resolve().parent.parent / "live_state.py"
DJANGO = "vasilistotskas/grooveshop-django-api"
STOREFRONT = "vasilistotskas/grooveshop-storefront-ui-node-nuxt"
GATEWAY = "vasilistotskas/grooveshop-agent-gateway"


def deployment(identifier: int, ref: str) -> dict:
    return {"id": identifier, "ref": ref}


class FakeGitHub(BaseHTTPRequestHandler):
    # (repository, environment) -> newest-first [(id, ref, latest status)]
    records: dict[tuple[str, str], list[tuple[int, str, str | None]]] = {}

    def do_GET(self) -> None:
        parts = urlparse(self.path)
        segments = parts.path.strip("/").split("/")
        # /repos/<owner>/<repo>/deployments[/<id>/statuses]
        repository = "/".join(segments[1:3])
        if len(segments) == 4:
            environment = parts.query.split("environment=")[1].split("&")[0]
            body = [
                deployment(identifier, ref)
                for identifier, ref, _ in self.records.get(
                    (repository, environment), []
                )
            ]
        else:
            identifier = int(segments[4])
            body = []
            for rows in self.records.values():
                for row_id, _, state in rows:
                    if row_id == identifier and state:
                        body = [{"state": state}]
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    # The parameter name is the base class's: a different one breaks the override.
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class LiveState(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def run_script(self, *environments: str) -> tuple[int, dict[str, str], str]:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "live.env"
            result = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), str(output), *environments],
                env={
                    **os.environ,
                    "GITHUB_API_URL": f"http://127.0.0.1:{self.server.server_port}",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            values = {}
            if output.exists():
                for line in output.read_text().splitlines():
                    key, value = line.split("=", 1)
                    values[key] = value.strip('"')
            return result.returncode, values, result.stderr

    def record(self, repository: str, environment: str, *rows: tuple) -> None:
        FakeGitHub.records[(repository, environment)] = list(rows)

    def setUp(self) -> None:
        FakeGitHub.records = {}
        for repository in (DJANGO, STOREFRONT, GATEWAY):
            self.record(repository, "production", (1, "v1.0.0", "success"))

    def test_newest_success_is_live(self) -> None:
        self.record(
            STOREFRONT,
            "production",
            (3, "v3.300.1", "success"),
            (2, "v3.299.0", "success"),
        )
        code, values, _ = self.run_script("production")
        self.assertEqual(code, 0)
        self.assertEqual(values["PRODUCTION_STOREFRONT"], "v3.300.1")
        self.assertEqual(values["PRODUCTION_DJANGO"], "v1.0.0")

    def test_a_rollback_is_a_newer_deployment_of_the_older_tag(self) -> None:
        self.record(
            DJANGO,
            "production",
            (9, "v3.203.1", "success"),
            (8, "v3.206.0", "success"),
        )
        _, values, _ = self.run_script("production")
        self.assertEqual(values["PRODUCTION_DJANGO"], "v3.203.1")

    def test_a_rollout_in_flight_is_checked_as_well(self) -> None:
        self.record(
            DJANGO,
            "production",
            (12, "v3.207.0", "in_progress"),
            (11, "v3.206.1", "queued"),
            (10, "v3.206.0", "success"),
        )
        _, values, _ = self.run_script("production")
        self.assertEqual(
            values["PRODUCTION_DJANGO"], "v3.206.0 v3.207.0 v3.206.1"
        )

    def test_a_failed_deployment_is_ignored(self) -> None:
        self.record(
            DJANGO,
            "production",
            (12, "v3.207.0", "failure"),
            (11, "v3.206.1", "error"),
            (10, "v3.206.0", "success"),
        )
        _, values, _ = self.run_script("production")
        self.assertEqual(values["PRODUCTION_DJANGO"], "v3.206.0")

    def test_an_inactive_production_deployment_is_not_needed(self) -> None:
        self.record(
            DJANGO,
            "production",
            (12, "v3.206.0", "success"),
            (11, "v3.205.0", "inactive"),
        )
        _, values, _ = self.run_script("production")
        self.assertEqual(values["PRODUCTION_DJANGO"], "v3.206.0")

    def test_every_environment_is_read_on_its_own(self) -> None:
        for repository in (DJANGO, STOREFRONT, GATEWAY):
            self.record(repository, "staging", (5, "v2.0.0", "success"))
        _, values, _ = self.run_script("production", "staging")
        self.assertEqual(values["STAGING_GATEWAY"], "v2.0.0")
        self.assertEqual(values["PRODUCTION_GATEWAY"], "v1.0.0")

    def test_no_successful_deployment_fails_closed(self) -> None:
        self.record(GATEWAY, "production", (1, "v1.0.0", "failure"))
        code, values, error = self.run_script("production")
        self.assertEqual(code, 1)
        self.assertEqual(values, {})
        self.assertIn("cannot say what is live", error)

    def test_a_missing_environment_fails_closed(self) -> None:
        code, _, error = self.run_script("staging")
        self.assertEqual(code, 1)
        self.assertIn("staging", error)

    def test_a_ref_that_is_not_a_release_tag_fails_closed(self) -> None:
        self.record(STOREFRONT, "production", (1, "main", "success"))
        code, _, error = self.run_script("production")
        self.assertEqual(code, 1)
        self.assertIn("not a release tag", error)

    def test_an_unreachable_api_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(SCRIPT),
                    str(Path(directory) / "x"),
                    "production",
                ],
                env={**os.environ, "GITHUB_API_URL": "http://127.0.0.1:9"},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read", result.stderr)


if __name__ == "__main__":
    unittest.main()
