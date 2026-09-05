"""A hard dependency must not be imported as though it were optional.

`try: import X / except ImportError:` around a package that is a
declared runtime dependency is dead code, and it is worse than merely
dead: it makes the `else` branch look like a supported configuration.
This codebase carried nine such guards — stub classes for mptt and
parler, a `UNFOLD_AVAILABLE` flag, `celery_available` in three signal
modules, "Wave 3 task not yet available" in a carrier — every one of
them for a package that cannot be absent.

The rule is anchored on `pyproject.toml`'s `[project].dependencies`,
because that is exactly the set the production image installs (`uv sync
--no-dev`). A guard around a *dev* dependency is legitimate and is not
flagged: `devtools/utils/profiler.py` guards `psutil` on purpose, since
the profiler is dev-only tooling that must not break a prod import.
"""

from __future__ import annotations

import ast
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_EXCLUDED_DIRS = frozenset(
    {".venv", "tests", "tests_mt", "migrations", "node_modules", ".git"}
)
# Django's own generated boilerplate. Its guard re-raises with an
# actionable message and is the documented way to write it.
_EXCLUDED_FILES = frozenset({"manage.py"})


def _runtime_import_names() -> frozenset[str]:
    """Top-level module names of the declared runtime dependencies.

    `pyproject.toml` names distributions (`django-extra-settings`);
    source imports name modules (`extra_settings`). `packages_distributions`
    is the standard-library map between the two.
    """
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared = {
        # "django-allauth[mfa]>=65" -> "django-allauth"
        spec.split("[")[0]
        .split(";")[0]
        .split("==")[0]
        .split(">=")[0]
        .split("<")[0]
        .split("~=")[0]
        .strip()
        .lower()
        for spec in pyproject["project"]["dependencies"]
    }
    return frozenset(
        module
        for module, dists in packages_distributions().items()
        if any(dist.lower() in declared for dist in dists)
    )


def _guarded_import_modules(tree: ast.AST) -> list[tuple[int, str]]:
    """Top-level module of every import inside a `try/except ImportError`."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import_error = any(
            handler.type is not None
            and "ImportError"
            in ast.unparse(handler.type)  # covers `(ImportError, X)` too
            for handler in node.handlers
        )
        if not catches_import_error:
            continue
        for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            match stmt:
                case ast.Import(names=names):
                    found.extend(
                        (stmt.lineno, alias.name.split(".")[0])
                        for alias in names
                    )
                case ast.ImportFrom(module=str() as module, level=0):
                    found.append((stmt.lineno, module.split(".")[0]))
    return found


def _source_files() -> list[Path]:
    return [
        path
        for path in REPO_ROOT.rglob("*.py")
        if not _EXCLUDED_DIRS & set(path.relative_to(REPO_ROOT).parts)
        and path.name not in _EXCLUDED_FILES
    ]


def test_no_runtime_dependency_is_imported_behind_an_importerror_guard():
    runtime = _runtime_import_names()
    assert "django" in runtime, (
        "Failed to resolve runtime dependencies to module names — the "
        "detector is broken, not the codebase."
    )

    offenders = []
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError, UnicodeDecodeError:  # pragma: no cover
            continue
        for lineno, module in _guarded_import_modules(tree):
            if module in runtime:
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno} guards `{module}`")

    assert not offenders, (
        "These imports are guarded against an ImportError that cannot "
        "happen — the package is a declared runtime dependency, so the "
        "process would not have started without it. Drop the guard and "
        "the branch it protects:\n  " + "\n  ".join(sorted(offenders))
    )
