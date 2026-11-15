#!/usr/bin/env bash
# Pairs the fixture documents in tests/fixtures/ through the `compare` entry
# point of api-contract.sh, in the pinned oasdiff image, as every caller runs
# it. Needs docker (to pull the image once) and nothing else: no document is
# fetched.
#
#   api-contract/tests/compare.sh
#
# consumer.yml is what a consumer generated its parsers from; each other
# fixture is it with one change. The nullability cases are #710: flattening
# drops a `nullable: true` beside an `allOf` (oasdiff/oasdiff#1348), so
# without the unflattened nullability pass the first two loosenings below
# pass as "No changes detected".
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image=$(cat "$here/oasdiff-image.txt")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# The rules the entry point runs: this directory, with nothing accepted.
mkdir -p "$work/rules"
cp "$here"/{api-contract.sh,services.txt,storefront-levels.txt,allauth-required.txt} "$work/rules/"
cp -r "$here/tests/fixtures" "$work/fixtures"

host_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }

# run CONSUMER DJANGO [LEVELS] -> sets code and output (paths under fixtures/)
run() {
  set +e
  output=$(MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(host_path "$work/rules"):/contract:ro" \
    -v "$(host_path "$work/fixtures"):/fixtures:ro" \
    -w /fixtures --entrypoint /bin/sh "$image" /contract/api-contract.sh compare "$@" 2>&1)
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

: > "$work/rules/ignore.txt"

run consumer.yml consumer.yml
expect "a document is compatible with itself" 0 "ok"

run consumer.yml response-nullable.yml
expect "a response object made nullable beside allOf is refused" 1 \
  "the response property \`author\` became nullable for the status \`200\`" \
  "the response property \`parent/user\` became nullable for the status \`200\`" \
  "[response-property-became-nullable]" "BREAKS"

run response-nullable.yml consumer.yml
expect "a response object no longer null is compatible" 0 "ok"

run consumer.yml request-not-nullable.yml
expect "a request object that no longer accepts null is refused" 1 \
  "the request property \`replyTo\` became not nullable" \
  "[request-property-became-not-nullable]"

run consumer.yml required-removed.yml
expect "a required property removed inside allOf is still refused flattened" 1 \
  "removed the required property \`author/name\`" "[response-required-property-removed]"

# Nullability belongs to the unflattened pass alone: a change the flattened
# pass would also see is printed once, in the unflattened wording, so the
# ignore.txt line copied from it is the only one needed.
run consumer.yml inner-nullable.yml
expect "a property made nullable inside an allOf is refused" 1 \
  "the response property \`author/allOf[#/components/schemas/User]/name\` became nullable" \
  "the response property \`parent/user/name\` became nullable"
if grep -qF -- "\`author/name\`" <<<"$output"; then
  echo "FAIL a nullability change is reported by both passes"; failed=1
else
  echo "ok   a nullability change is reported by one pass only"
fi

# The consumer's own levels decide, in the nullability pass as in the first.
printf 'response-property-became-nullable INFO\n' > "$work/fixtures/lenient-levels.txt"
run consumer.yml response-nullable.yml lenient-levels.txt
expect "a consumer that tolerates null in responses is not refused" 0 "ok"

# An accepted break is accepted in the nullability pass too.
cat > "$work/rules/ignore.txt" <<'EOF'
2026-11-15 until storefront v9.9.9 | fixture | GET /comment the response property `author` became nullable for the status `200`
2026-11-15 until storefront v9.9.9 | fixture | GET /comment the response property `parent/user` became nullable for the status `200`
2026-11-15 until storefront v9.9.9 | fixture | POST /comment the response property `author` became nullable for the status `201`
2026-11-15 until storefront v9.9.9 | fixture | POST /comment the response property `parent/user` became nullable for the status `201`
EOF
run consumer.yml response-nullable.yml
expect "ignore.txt accepts a nullability break" 0 "ok"

cat > "$work/rules/ignore.txt" <<'EOF'
2026-11-15 until storefront v9.9.9 | fixture | GET /comment the response property `author/allOf[#/components/schemas/User]/name` became nullable for the status `200`
2026-11-15 until storefront v9.9.9 | fixture | GET /comment the response property `parent/user/name` became nullable for the status `200`
2026-11-15 until storefront v9.9.9 | fixture | POST /comment the response property `author/allOf[#/components/schemas/User]/name` became nullable for the status `201`
2026-11-15 until storefront v9.9.9 | fixture | POST /comment the response property `parent/user/name` became nullable for the status `201`
EOF
run consumer.yml inner-nullable.yml
expect "ignore.txt accepts what the nullability pass printed" 0 "ok"

exit "$failed"
