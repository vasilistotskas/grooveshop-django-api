# Test Runner

Run targeted tests for modified apps in the grooveshop-django-api project.

## App-to-Test Mapping

| Modified App | Unit Tests | Integration Tests | Command |
|---|---|---|---|
| `product/` | `tests/unit/product/` | `tests/integration/product/` | `uv run pytest tests/unit/product/ tests/integration/product/` |
| `order/` | `tests/unit/order/` | `tests/integration/order/` | `uv run pytest tests/unit/order/ tests/integration/order/` |
| `cart/` | `tests/unit/cart/` | `tests/integration/cart/` | `uv run pytest tests/unit/cart/ tests/integration/cart/` |
| `blog/` | `tests/unit/blog/` | `tests/integration/blog/` | `uv run pytest tests/unit/blog/ tests/integration/blog/` |
| `user/` | `tests/unit/user/` | `tests/integration/user/` | `uv run pytest tests/unit/user/ tests/integration/user/` |
| `authentication/` | `tests/unit/authentication/` | `tests/integration/authentication/` | `uv run pytest tests/unit/authentication/ tests/integration/authentication/` |
| `search/` | `tests/unit/search/` | `tests/integration/search/` | `uv run pytest tests/unit/search/ tests/integration/search/` |
| `meili/` | `tests/unit/meili/` | `tests/integration/meili/` | `uv run pytest tests/unit/meili/ tests/integration/meili/` |
| `notification/` | `tests/unit/notification/` | `tests/integration/notification/` | `uv run pytest tests/unit/notification/ tests/integration/notification/` |
| `loyalty/` | `tests/unit/test_loyalty/` | `tests/integration/loyalty/` | `uv run pytest tests/unit/test_loyalty/ tests/integration/loyalty/` |
| `core/` | `tests/unit/core/` | `tests/integration/core/` | `uv run pytest tests/unit/core/ tests/integration/core/` |
| `country/` | `tests/unit/country/` | `tests/integration/country/` | `uv run pytest tests/unit/country/ tests/integration/country/` |
| `region/` | `tests/unit/region/` | `tests/integration/region/` | `uv run pytest tests/unit/region/ tests/integration/region/` |
| `vat/` | `tests/unit/vat/` | `tests/integration/vat/` | `uv run pytest tests/unit/vat/ tests/integration/vat/` |
| `pay_way/` | `tests/unit/pay_way/` | `tests/integration/pay_way/` | `uv run pytest tests/unit/pay_way/ tests/integration/pay_way/` |
| `tag/` | `tests/unit/tag/` | `tests/integration/tag/` | `uv run pytest tests/unit/tag/ tests/integration/tag/` |
| `contact/` | `tests/unit/contact/` | `tests/integration/contact/` | `uv run pytest tests/unit/contact/ tests/integration/contact/` |
| `devtools/` | `tests/unit/devtools/` | — | `uv run pytest tests/unit/devtools/` |
| Cross-app changes | All tests | All tests | `uv run pytest` |

## Process

1. Identify which app directories were modified using `git diff --name-only`
2. Map each modified app to its test directories using the table above
3. If a test directory doesn't exist, skip it (not all apps have both unit and integration tests)
4. Run targeted test commands (parallel execution is the default via `-n auto` in addopts)
5. For faster feedback on a single file, add `-n0` to disable parallel: `uv run pytest tests/unit/product/test_models.py -n0`
6. If a test fails, report:
   - Test file path and test name
   - Error message and stack trace
   - The source file and line that likely caused the failure
   - Suggested fix if the cause is clear

## Prerequisites

- PostgreSQL must be running (tests use a real database)
- Redis must be running (used for caching, even though `DISABLE_CACHE = True` is set in conftest)
- Meilisearch is NOT required (tests run with `MEILISEARCH["OFFLINE"] = True`)
- Celery tasks run synchronously (`CELERY_TASK_ALWAYS_EAGER = True`)

## Useful Options

- **Run only unit tests**: `uv run pytest tests/unit/`
- **Run only integration tests**: `uv run pytest tests/integration/`
- **Verbose output**: add `-v` flag
- **Stop on first failure**: add `-x` flag
- **Show local variables on failure**: add `-l` flag
- **Run tests matching a pattern**: `uv run pytest -k "test_list" tests/unit/product/`
- **Skip Meilisearch tests**: they auto-skip when offline (via `requires_meilisearch` marker)
- **Coverage for a single app**: `uv run pytest tests/unit/product/ --cov=product --cov-report=term -n0`

## Notes

- Tests run in parallel by default (`-n auto` via pytest-xdist)
- Test timeout is 600 seconds per test
- Password hashing uses MD5 (faster than default bcrypt)
- Coverage minimum is 50% (`fail_under = 50`)
- The `count_queries` fixture uses `.query_count` attribute; the standalone `QueryCountAssertion` uses `.count`
