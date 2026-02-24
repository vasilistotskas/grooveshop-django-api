---
name: gen-test
description: >
  Generate pytest tests for the GrooveShop Django API following all project
  conventions: CustomDjangoModelFactory usage, APITestCase with setUpTestData,
  query counting for N+1 detection, proper authentication patterns, and
  assertion conventions. Use this skill whenever the user wants to generate
  tests, write tests, add test coverage, or says things like "test this",
  "add tests for X", "write tests for the Y endpoint", "generate unit tests",
  "cover this model with tests". Also use when the user asks to test factories,
  managers, signals, serializers, or views.
disable-model-invocation: true
---

# Generate Tests

Generate tests that match every convention in this codebase.

## Before You Start

1. **Identify the target**: What are we testing? A model, manager, serializer, viewset, factory, or signal?
2. **Read the source code** of the target module to understand its behavior.
3. **Read the existing factory** for the model (if it exists) — it shows how to create test data.
4. **Check for existing tests** in `tests/unit/{app}/` and `tests/integration/{app}/` to match style.

## Test File Placement

```
tests/
├── unit/{app}/           # No DB access or minimal DB (model logic, utils, pure functions)
│   ├── test_models.py
│   ├── test_managers.py
│   ├── test_factories.py
│   ├── test_signals.py
│   └── test_serializers.py
└── integration/{app}/    # Full DB + API client (endpoint tests)
    └── test_view_{entity}.py
```

Naming conventions:
- Test files: `test_{entity}.py` or `test_view_{entity}.py` (for view tests)
- Test classes: `{Entity}ViewSetTestCase`, `Test{Entity}Factory`, `Test{Entity}Manager`
- Test methods: `test_{action}_{scenario}` (e.g., `test_list_returns_paginated_results`)

## What to Generate Per Target Type

| Target | Test Class Base | Key Assertions |
|--------|----------------|----------------|
| **Factory** | `TestCase` | Instance created, fields populated, translations exist, get_or_create works |
| **Manager** | `TestCase` | QuerySet methods return correct results, `for_list()`/`for_detail()` optimize queries |
| **Model** | `TestCase` | Validation, computed properties, `__str__`, save behavior |
| **Serializer** | `TestCase` | Field presence, validation errors, read_only enforcement |
| **ViewSet (CRUD)** | `APITestCase` | Status codes, response structure, permissions, pagination |
| **ViewSet (custom action)** | `APITestCase` | Action-specific logic, permissions, response format |
| **Signal** | `TestCase` | Signal fires, side effects occur, async signals use mocks |

For detailed patterns and templates, read `references/patterns.md`.

## Critical Conventions

These conventions are non-negotiable — every generated test must follow them:

1. **Use factories**, never raw `Model.objects.create()` for complex objects (factories handle translations, unique fields, and related objects correctly)
2. **Use `setUpTestData()`** (class method) for read-only test data; use `setUp()` only when tests modify data
3. **Use `reverse()`** for all URL generation — never hardcode URL paths
4. **Use `self.client.force_authenticate(user=user)`** for authentication
5. **Assert response structure**: check `results`, `count`, `links` keys in list responses
6. **Assert field presence**: verify expected fields exist in response data
7. **Status code constants**: use `status.HTTP_200_OK` not `200`
8. **Query counting**: use `count_queries` fixture or `assertNumQueries` for manager/view tests

## Output Checklist

After generating tests, verify:
- [ ] All test classes have descriptive names following naming conventions
- [ ] Factories are imported from the correct app (not recreated)
- [ ] `@pytest.mark.django_db` is used for pytest-style tests needing DB access
- [ ] `setUpTestData` vs `setUp` is chosen correctly
- [ ] URL names match the actual `name=` in the app's `urls.py`
- [ ] Permissions are tested (authenticated, unauthenticated, wrong user, admin)
- [ ] Both success and error paths are covered
- [ ] No hardcoded IDs, URLs, or magic numbers
