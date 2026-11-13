# shellcheck shell=bash
# The paths a docs-only change touches: the one definition of what the CI
# workflow treats as "no code". Sourced by release_target.sh and
# pr_touches_code.sh. ``IGNORED`` mirrors ``on.push.paths-ignore`` in ci.yml;
# tests/unit/core/test_release_target_ignores.py fails when they differ.

IGNORED=('**.md' 'docs/**' 'LICENSE' '.editorconfig')

ignored_path() {
  # The four patterns as GitHub reads them: ``**.md`` and ``docs/**``
  # cross directories, the other two are exact.
  case $1 in
    *.md | docs/* | LICENSE | .editorconfig) return 0 ;;
  esac
  return 1
}
