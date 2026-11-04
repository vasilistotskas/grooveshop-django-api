"""check-ignore.py: entries expire when the old side is no longer live."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "check-ignore.py"
ENTRY = (
    "2026-11-03 until storefront v3.304.0 | owner-approved, a reason | "
    "GET /api/v1/order/viva_return the response body was restructured"
)


def run(ignore: str, live: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as directory:
        ignore_file = Path(directory) / "ignore.txt"
        live_file = Path(directory) / "live.env"
        ignore_file.write_text(ignore, encoding="utf-8")
        live_file.write_text(live, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--ignore",
                str(ignore_file),
                "--live",
                str(live_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    return result.returncode, result.stderr


class CheckIgnore(unittest.TestCase):
    def test_an_entry_before_its_until_release_is_current(self) -> None:
        code, _ = run(ENTRY + "\n", 'PRODUCTION_STOREFRONT="v3.303.1"\n')
        self.assertEqual(code, 0)

    def test_an_entry_at_its_until_release_is_expired(self) -> None:
        code, error = run(ENTRY + "\n", 'PRODUCTION_STOREFRONT="v3.304.0"\n')
        self.assertEqual(code, 1)
        self.assertIn("expired", error)

    def test_a_rollout_in_flight_keeps_the_lowest_tag(self) -> None:
        code, _ = run(
            ENTRY + "\n", 'PRODUCTION_STOREFRONT="v3.303.1 v3.305.0"\n'
        )
        self.assertEqual(code, 0)

    def test_numbers_compare_as_numbers_not_text(self) -> None:
        code, _ = run(ENTRY + "\n", 'PRODUCTION_STOREFRONT="v3.99.0"\n')
        self.assertEqual(code, 0)

    def test_only_production_decides(self) -> None:
        code, _ = run(
            ENTRY + "\n",
            'PRODUCTION_STOREFRONT="v3.303.1"\nSTAGING_STOREFRONT="v3.304.0"\n',
        )
        self.assertEqual(code, 0)

    def test_a_line_without_until_is_refused(self) -> None:
        code, error = run(
            "2026-11-03 a reason: GET /api/v1/x removed\n",
            'PRODUCTION_STOREFRONT="v3.1.0"\n',
        )
        self.assertEqual(code, 1)
        self.assertIn("not '<date> until", error)

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        code, _ = run(
            "# header\n\n" + ENTRY + "\n", 'PRODUCTION_STOREFRONT="v3.1.0"\n'
        )
        self.assertEqual(code, 0)

    def test_an_unrecorded_service_fails(self) -> None:
        code, error = run(ENTRY + "\n", 'PRODUCTION_DJANGO="v3.1.0"\n')
        self.assertEqual(code, 1)
        self.assertIn("no production release is recorded", error)

    def test_missing_files_cannot_be_checked(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--ignore",
                "/nope",
                "--live",
                "/nope",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
