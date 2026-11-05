#!/usr/bin/env bash
# Replays the 2026-11-03 deadlock (Django #442 / storefront #347) through the
# PR entry point of api-contract.sh, against the real released documents.
#
#   api-contract/tests/replay.sh
#
# Needs docker and the network (the documents are fetched at their tags,
# which are immutable, so the expected verdicts are fixed). It runs the
# script in the pinned oasdiff image, as every caller does, with an EMPTY
# ignore.txt so that no accepted break hides one.
#
# Live on 2026-11-03 before the incident: Django v3.203.1, storefront
# v3.299.0. #442 shipped as Django v3.206.0 (a required `restoredFrom`, a
# `restore` enum value), #347 as storefront v3.300.1 (requires `restoredFrom`).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image=$(cat "$here/oasdiff-image.txt")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

DJANGO=https://raw.githubusercontent.com/vasilistotskas/grooveshop-django-api
STOREFRONT=https://raw.githubusercontent.com/vasilistotskas/grooveshop-storefront-ui-node-nuxt

# The rules the PR entry point runs: this directory, with nothing accepted.
mkdir -p "$work/rules" "$work/live"
cp "$here"/{api-contract.sh,services.txt,storefront-levels.txt} "$work/rules/"
: > "$work/rules/ignore.txt"
# These tags predate the storefront's allauth document.
echo false > "$work/rules/allauth-required.txt"

host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }

# live ENV=SERVICE:TAG...  writes live.env with production and staging
live() {
  : > "$work/live/live.env"
  for entry in "$@"; do
    environment=${entry%%=*}
    for pair in ${entry#*=}; do
      printf '%s_%s="%s"\n' "$(tr '[:lower:]' '[:upper:]' <<<"$environment")" \
        "$(tr '[:lower:]' '[:upper:]' <<<"${pair%%:*}")" "${pair#*:}" >> "$work/live/live.env"
    done
  done
}

# run KIND ROOT ENVIRONMENT... -> sets code and output
run() {
  set +e
  output=$(MSYS_NO_PATHCONV=1 docker run --rm -e LIVE_FILE=/live/live.env \
    -v "$(host_path "$work/rules"):/contract:ro" \
    -v "$(host_path "$work/live"):/live:ro" \
    -v "$(host_path "$1"):/repo:ro" \
    --entrypoint /bin/sh "$image" /contract/api-contract.sh pr "$2" /repo "${@:3}" 2>&1)
  code=$?
  set -e
}

expect() { # NAME EXPECTED_CODE [TEXT...]
  name=$1 want=$2
  shift 2
  if [[ $code -ne $want ]]; then
    echo "FAIL $name: exit $code, wanted $want"; echo "$output" | cut -c1-200 | head -30; failed=1; return
  fi
  for text in "$@"; do
    grep -qF -- "$text" <<<"$output" || { echo "FAIL $name: output lacks '$text'"; failed=1; return; }
  done
  echo "ok   $name"
}

failed=0
fetch() { mkdir -p "$work/$1/$(dirname "$3")"; curl -fsS "$2" -o "$work/$1/$3"; }

live production=django:v3.203.1\ storefront:v3.299.0\ gateway:v1.35.0 \
     staging=django:v3.203.1\ storefront:v3.299.0\ gateway:v1.35.0

# 1. The pair that was live is compatible.
fetch django-v3.203.1 "$DJANGO/v3.203.1/schema.yml" schema.yml
run "$work/django-v3.203.1" django production staging
expect "baseline: Django v3.203.1 against the live storefront" 0 "ok"

# 2. #442 as a PR: refused, naming the enum value and the live storefront.
fetch django-v3.206.0 "$DJANGO/v3.206.0/schema.yml" schema.yml
run "$work/django-v3.206.0" django production staging
expect "#442 (Django v3.206.0) is refused against storefront v3.299.0" 1 \
  "Django (this PR) -> storefront v3.299.0 [production]" \
  "added the new \`restore\` enum value" "API CONTRACT: this PR breaks a LIVE consumer"

# 3. #347 as a PR: refused, naming the field live Django does not provide.
fetch storefront-v3.300.1 "$STOREFRONT/v3.300.1/openapi/schema.yml" openapi/schema.yml
run "$work/storefront-v3.300.1" storefront production staging
expect "#347 (storefront v3.300.1) is refused against Django v3.203.1" 1 \
  "storefront (this PR) <- Django v3.203.1 [production]" \
  "removed the required property \`restoredFrom\`" \
  "this PR requires something the LIVE Django does not provide"

# 4. Staging ahead of production: the environment is named in the verdict.
live production=django:v3.203.1\ storefront:v3.299.0\ gateway:v1.35.0 \
     staging=django:v3.203.1\ storefront:v3.300.1\ gateway:v1.35.0
run "$work/django-v3.203.1" django production staging
expect "Django v3.203.1 breaks the storefront staging already runs" 1 "[staging]" "restoredFrom"

# 5. No answer to "what is live" is no verdict.
printf 'PRODUCTION_DJANGO="v3.203.1"\n' > "$work/live/live.env"
run "$work/django-v3.203.1" django production
expect "no live storefront recorded fails closed" 2 "records no live storefront"

exit "$failed"
