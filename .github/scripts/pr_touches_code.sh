#!/usr/bin/env bash
# Prints ``code=true`` when a pull request changes anything outside the
# ignored paths (.github/scripts/ignored_paths.sh), else ``code=false``.
#
#   pr_touches_code.sh
#
# Run in a checkout of the pull_request merge ref with two commits of
# history: HEAD is the merge of the PR head into the base, so
# ``HEAD^1..HEAD`` is exactly what merging adds to main. ``--no-renames``
# lists a file moved into an ignored path under its old path too, so a
# move out of code is never mistaken for a docs change.
set -euo pipefail

# shellcheck source=ignored_paths.sh
source "$(dirname "${BASH_SOURCE[0]}")/ignored_paths.sh"

# Captured first, so a failing ``git diff`` fails the script (the exit
# status of a process substitution is lost): an unreadable diff must never
# read as "nothing but docs changed".
changed=$(git diff --no-renames --name-only HEAD^1 HEAD)

while IFS= read -r file; do
  [[ -z $file ]] && continue
  if ! ignored_path "$file"; then
    echo "code changed: $file" >&2
    echo "code=true"
    exit 0
  fi
done <<<"$changed"

echo "only ignored paths changed" >&2
echo "code=false"
