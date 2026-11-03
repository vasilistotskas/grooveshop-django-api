#!/bin/sh
# Can this Django API and its consumers be live at the same time?
#
# The single implementation of the API contract rule. Three callers run it,
# always in the pinned oasdiff image (oasdiff-image.txt), so a verdict is
# the same wherever it is asked for:
#
#   * the deploy gate: grooveshop-infrastructure's backend-api-contract
#     PreSync Job, from the files at the Django tag being deployed;
#   * Django's PR check (.github/workflows/api-contract.yml), from the PR;
#   * the storefront's and the gateway's PR checks, from the Django release
#     that is live in each environment.
#
#   api-contract.sh deploy DJANGO_LIVE DJANGO_TARGET STOREFRONT_LIVE \
#                          STOREFRONT_TARGET GATEWAY_LIVE GATEWAY_TARGET
#   api-contract.sh pr KIND REPO_ROOT ENVIRONMENT...
#
# Consumers parse Django's responses:
#
#   * storefront: answers 422 to any response its generated Zod schemas
#     reject; they come from the OpenAPI document it commits
#     (openapi/schema.yml).
#   * allauth: the storefront also generates its allauth (sign-in, sign-up,
#     2FA, passkeys) parsers from allauth's headless OpenAPI document,
#     which Django commits as allauth-schema.yml and the storefront as
#     openapi/allauth-schema.yml. While allauth-required.txt is not ``true`` a
#     document missing at a tag (HTTP 404: a tag from before the file) is
#     reported as not checked; once the storefront commits one, set it, and
#     a missing document fails the check, because an unchecked sign-in
#     contract is not a safe default.
#   * gateway: decodes into Go types, and Go zero-fills a missing field
#     silently (a dropped price becomes 0 in a feed). It commits the slice
#     of Django it reads, generated from those types, as
#     contract/django-consumer.openapi.json, with the oasdiff severity
#     overrides that contract needs (contract/oasdiff-levels.txt).
#
# ``oasdiff breaking BASE REVISION`` reports what in REVISION breaks a
# client of BASE, so BASE is the consumer's document and REVISION is
# Django's.
#
# deploy: a sync rolls every Deployment independently, so while it runs any
# live/target combination can serve traffic. For each consumer every
# pairing that differs from what runs now is checked: target Django with
# the live consumer, live Django with the target consumer, and target with
# target. An empty LIVE tag (first bring-up) drops the pairings it is part
# of.
#
# pr: KIND is the repository the PR belongs to (django, storefront or
# gateway); REPO_ROOT is its checkout. The PR's own documents are paired
# with the other side as the environments run it NOW: the tags in
# LIVE_FILE, written by live-state.sh from the GitHub Deployments the
# cluster reconciler records. Each environment's newest successful
# deployment is live; a newer one still rolling out is checked as well.
# Every environment named is checked, and one that holds the same tags as
# one already checked is skipped.
#
# A gateway tag that predates its contract file (HTTP 404 on the levels
# file) is reported as not checked; any other failure to fetch it fails.
#
# Exit 0 = compatible, 1 = a pairing breaks, anything else = the check
# could not run (a sync or a merge must not proceed on an unanswered
# question).
#
# Deliberate breaks are accepted line by line in ignore.txt (oasdiff's
# --err-ignore format), each with a date, an ``until`` release and a
# reason, so every acceptance is reviewed with the change that needs it and
# expires when the old side is no longer live (check-ignore.py).
set -u

RULES_DIR=$(cd "$(dirname "$0")" && pwd)
IGNORE_FILE="$RULES_DIR/ignore.txt"
STOREFRONT_LEVELS_FILE="$RULES_DIR/storefront-levels.txt"

# Every repository is public; each tag's document is fetched as committed.
# services.txt names the repositories once.
repo_of() { sed -n "s/^$1 //p" "$RULES_DIR/services.txt"; }
RAW=https://raw.githubusercontent.com
at() { printf %s "${1%%@TAG@*}$2${1#*@TAG@}"; } # TEMPLATE TAG
DJANGO_SCHEMA_URL="$RAW/$(repo_of django)/@TAG@/schema.yml"
DJANGO_ALLAUTH_URL="$RAW/$(repo_of django)/@TAG@/allauth-schema.yml"
STOREFRONT_SCHEMA_URL="$RAW/$(repo_of storefront)/@TAG@/openapi/schema.yml"
STOREFRONT_ALLAUTH_URL="$RAW/$(repo_of storefront)/@TAG@/openapi/allauth-schema.yml"
GATEWAY_CONTRACT_URL="$RAW/$(repo_of gateway)/@TAG@/contract/django-consumer.openapi.json"
GATEWAY_LEVELS_URL="$RAW/$(repo_of gateway)/@TAG@/contract/oasdiff-levels.txt"
# Whether a missing allauth document fails the check or is reported as not
# checked: ``false`` until the storefront commits one. Read from the rules,
# so the deploy gate and every PR check agree.
ALLAUTH_REQUIRED=$(tr -d ' \r\n' < "$RULES_DIR/allauth-required.txt")
LIVE_FILE=${LIVE_FILE:-}
WORK_DIR=${TMPDIR:-/tmp}
# oasdiff exits 102 when it cannot load a document (a network failure
# among them). That one is retried once; a verdict never is.
LOAD_FAILED=102
NOT_CHECKED=3

[ -s "$STOREFRONT_LEVELS_FILE" ] || {
    echo "api-contract: $STOREFRONT_LEVELS_FILE is missing: it ships beside this script" >&2
    exit 2
}

run_oasdiff() {
    base=$1 revision=$2
    shift 2
    attempt=1
    while :; do
        oasdiff breaking --allow-external-refs=false --fail-on ERR --format singleline \
            "$@" -- "$base" "$revision"
        code=$?
        [ "$code" -eq "$LOAD_FAILED" ] && [ "$attempt" -eq 1 ] || return "$code"
        echo "api-contract: could not load a document, retrying once in 10s" >&2
        attempt=2
        sleep 10
    done
}

# Document locations. A document is a URL (a tag's file) or a path (a PR's
# checkout); oasdiff loads both.
django_document() { # CONSUMER DJANGO_TAG
    case $1 in
        allauth) at "$DJANGO_ALLAUTH_URL" "$2" ;;
        *) at "$DJANGO_SCHEMA_URL" "$2" ;;
    esac
}

consumer_document() { # CONSUMER CONSUMER_TAG
    case $1 in
        storefront) at "$STOREFRONT_SCHEMA_URL" "$2" ;;
        allauth) at "$STOREFRONT_ALLAUTH_URL" "$2" ;;
        gateway) at "$GATEWAY_CONTRACT_URL" "$2" ;;
    esac
}

# The service whose tag a consumer's document is read at.
consumer_service() {
    case $1 in
        gateway) echo gateway ;;
        *) echo storefront ;;
    esac
}

# fetch_document URL DESTINATION LABEL: 0 fetched; NOT_CHECKED when the
# document does not exist (404) and ALLAUTH_REQUIRED is not true; 2
# otherwise (a document that should exist, or any other failure).
fetch_document() {
    url=$1 dest=$2 label=$3
    [ -s "$dest" ] && return 0
    if ! err=$(wget -q -O "$dest" "$url" 2>&1); then
        rm -f "$dest"
        case "$err" in
            *404*) document_missing "$label" "$url"; return $? ;;
            *) echo "   could not fetch $label: $err" >&2
               return 2 ;;
        esac
    fi
}

document_missing() { # LABEL WHERE
    if [ "$ALLAUTH_REQUIRED" != "true" ]; then
        echo "   $1 does not exist: not checked"
        return "$NOT_CHECKED"
    fi
    echo "   $1 does not exist, and it is required: $2" >&2
    return 2
}

# resolve_document LOCATION LABEL: sets DOC to something oasdiff can load,
# or returns NOT_CHECKED / 2 when the document is missing.
resolve_document() {
    loc=$1 label=$2
    case $loc in
        http*)
            DOC="$WORK_DIR/doc-$(printf %s "$loc" | cksum | cut -d' ' -f1).yml"
            fetch_document "$loc" "$DOC" "$label" ;;
        *)
            DOC=$loc
            [ -s "$loc" ] || { document_missing "$label" "$loc"; return $?; } ;;
    esac
}

# gateway_levels GATEWAY_TAG: sets LEVELS to the gateway's severity levels
# at that tag.
gateway_levels() {
    LEVELS="$WORK_DIR/gateway-levels-$1.txt"
    [ -s "$LEVELS" ] && return 0
    if ! err=$(wget -q -O "$LEVELS" "$(at "$GATEWAY_LEVELS_URL" "$1")" 2>&1); then
        rm -f "$LEVELS"
        case "$err" in
            *404*) echo "   gateway $1 publishes no consumer contract: not checked"
                   return "$NOT_CHECKED" ;;
            *) echo "   could not fetch gateway $1's levels file: $err" >&2
               return 2 ;;
        esac
    fi
}

# breaks CONSUMER DJANGO_DOCUMENT CONSUMER_DOCUMENT LEVELS_FILE
#
# Flattened: drf-spectacular wraps every nested serializer in an `allOf`
# branch, and unflattened oasdiff reports a required property removed from
# one only as a WARNING ("another branch may still guarantee it"), which
# --fail-on ERR lets through. Django v3.157.0 removed `preferences.cart` /
# `.catalogue` that way, the gate passed it, and every staging store
# answered "Store not found" (2026-10-27). Flattened, the same pair is two
# errors. The gateway's contract is generated from Go types, so the same
# flattening applies, and its own levels file decides what a Go client can
# and cannot notice (see the gateway's CLAUDE.md).
#
# The storefront parses every 2xx of an operation with the one Zod schema
# generated for it, so a success status Django adds is a body the live
# storefront rejects with a 422. oasdiff rates that INFO;
# storefront-levels.txt raises it to ERR (a guest's 202 for a product
# alert, 2026-10-30, would have passed).
breaks() {
    consumer=$1 django=$2 document=$3 levels=$4
    if [ "$consumer" = allauth ]; then
        resolve_document "$document" "the storefront's allauth document" || return $?
        document=$DOC
        resolve_document "$django" "Django's allauth document" || return $?
        django=$DOC
    fi
    set -- --flatten-allof --severity-levels "$levels"
    [ -s "$IGNORE_FILE" ] && set -- "$@" --err-ignore "$IGNORE_FILE"
    run_oasdiff "$document" "$django" "$@"
}

status=0
record() {
    code=$1
    case "$code" in
        0) echo "   ok" ;;
        1) echo "   BREAKS: this consumer would mishandle these responses"; status=1 ;;
        "$NOT_CHECKED") ;;
        *) echo "   could not check (exit $code)" >&2; [ "$status" -eq 1 ] || status=$code ;;
    esac
}

levels_for() { # CONSUMER CONSUMER_TAG: sets LEVELS
    case $1 in
        gateway) gateway_levels "$2" ;;
        *) LEVELS=$STOREFRONT_LEVELS_FILE ;;
    esac
}

checked=""
seen() { # KEY: 0 when already checked, else records it
    case " $checked " in *" $1 "*) return 0 ;; esac
    checked="$checked $1"
    return 1
}

# ---- deploy -------------------------------------------------------------

# check_consumer CONSUMER LIVE_TAG TARGET_TAG (the consumer's own tags)
check_consumer() {
    consumer=$1 consumer_live=$2 consumer_target=$3
    for pair in \
        "$DJANGO_TARGET $consumer_live" \
        "$DJANGO_LIVE $consumer_target" \
        "$DJANGO_TARGET $consumer_target"; do
        django=${pair% *} tag=${pair#* }
        [ -n "$django" ] && [ -n "$tag" ] || continue
        [ "$django" = "$DJANGO_LIVE" ] && [ "$tag" = "$consumer_live" ] && continue
        seen "$consumer:$django+$tag" && continue
        echo "== django $django + $consumer $tag"
        levels_for "$consumer" "$tag"
        code=$?
        if [ "$code" -eq 0 ]; then
            breaks "$consumer" "$(django_document "$consumer" "$django")" \
                "$(consumer_document "$consumer" "$tag")" "$LEVELS"
            code=$?
        fi
        record "$code"
    done
}

deploy() {
    DJANGO_LIVE=$1 DJANGO_TARGET=$2
    STOREFRONT_LIVE=$3 STOREFRONT_TARGET=$4
    GATEWAY_LIVE=${5:-} GATEWAY_TARGET=${6:-}
    [ -n "$DJANGO_TARGET" ] && [ -n "$STOREFRONT_TARGET" ] && [ -n "$GATEWAY_TARGET" ] || {
        echo "api-contract: the django, storefront and gateway target tags are required" >&2
        exit 2
    }
    check_consumer storefront "$STOREFRONT_LIVE" "$STOREFRONT_TARGET"
    check_consumer gateway "$GATEWAY_LIVE" "$GATEWAY_TARGET"
    check_consumer allauth "$STOREFRONT_LIVE" "$STOREFRONT_TARGET"
    [ -n "$checked" ] || echo "== nothing changed: django $DJANGO_TARGET, storefront $STOREFRONT_TARGET and gateway $GATEWAY_TARGET already live"
    if [ "$status" -eq 1 ]; then
        cat >&2 <<'EOF'

Refused. A Django release that removes or loosens a field a consumer reads
must wait until a release of that consumer that no longer needs it is in
production: ship the storefront (or gateway) first, then the Django
release. To accept a break on purpose, add the line oasdiff printed to
api-contract/ignore.txt in the Django repository, in the pull request that
makes the change, with its date, an `until` release and the reason.
EOF
    fi
    exit "$status"
}

# ---- pr -----------------------------------------------------------------

# live_tags ENVIRONMENT SERVICE: the tags LIVE_FILE records, live first.
live_tags() {
    key=$(printf %s_%s "$1" "$2" | tr '[:lower:]' '[:upper:]')
    line=$(sed -n "s/^$key=\"\(.*\)\"$/\1/p" "$LIVE_FILE")
    tags=""
    for tag in $line; do
        printf %s "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' || {
            echo "api-contract: $key holds '$tag', not a release tag" >&2
            return 2
        }
        tags="$tags $tag"
    done
    [ -n "$tags" ] || {
        echo "api-contract: $LIVE_FILE records no live $2 for $1: cannot say what this PR must work with" >&2
        return 2
    }
    printf '%s
' "${tags# }"
}

pr_fix() { # KIND
    case $1 in
        django) cat >&2 <<'EOF'

API CONTRACT: this PR breaks a LIVE consumer.
Ship the consumer first, then this change:
  1. Split the PR: keep only what the live consumer tolerates, or the
     enum / removal / loosening part alone.
  2. Regenerate the consumer from THIS branch (storefront: pnpm openapi-ts
     against this schema.yml), make its own check pass, deploy it.
  3. Re-run this check; it passes once the consumer that is live accepts
     the change.
A deliberate break with no safe order (a route nothing calls) needs a dated
`until` entry in api-contract/ignore.txt in THIS pull request.
EOF
        ;;
        *) cat >&2 <<'EOF'

API CONTRACT: this PR requires something the LIVE Django does not provide.
Ship Django first, then this change:
  1. Regenerate from a Django schema WITHOUT the new requirement (or make
     the field optional) and deploy that.
  2. After the Django release that serves the field is live, open the PR
     that requires it.
  3. Re-run this check.
EOF
        ;;
    esac
}

pr() {
    kind=$1 root=$2
    shift 2
    [ -n "$LIVE_FILE" ] && [ -s "$LIVE_FILE" ] || {
        echo "api-contract: LIVE_FILE must name the file live-state.sh wrote" >&2
        exit 2
    }
    for environment in "$@"; do
        case $kind in
            django)
                for consumer in storefront allauth gateway; do
                    tags=$(live_tags "$environment" "$(consumer_service "$consumer")") || exit 2
                    for tag in $tags; do
                        seen "$consumer:$tag" && continue
                        echo "== Django (this PR) -> $consumer $tag [$environment]"
                        levels_for "$consumer" "$tag"
                        code=$?
                        if [ "$code" -eq 0 ]; then
                            case $consumer in allauth) mine="$root/allauth-schema.yml" ;; *) mine="$root/schema.yml" ;; esac
                            breaks "$consumer" "$mine" "$(consumer_document "$consumer" "$tag")" "$LEVELS"
                            code=$?
                        fi
                        record "$code"
                    done
                done ;;
            storefront|gateway)
                tags=$(live_tags "$environment" django) || exit 2
                for tag in $tags; do
                    for consumer in $( [ "$kind" = gateway ] && echo gateway || echo storefront allauth ); do
                        seen "$consumer:$tag" && continue
                        echo "== $consumer (this PR) <- Django $tag [$environment]"
                        case $consumer in
                            storefront) mine="$root/openapi/schema.yml"; LEVELS=$STOREFRONT_LEVELS_FILE ;;
                            allauth) mine="$root/openapi/allauth-schema.yml"; LEVELS=$STOREFRONT_LEVELS_FILE ;;
                            gateway) mine="$root/contract/django-consumer.openapi.json"; LEVELS="$root/contract/oasdiff-levels.txt" ;;
                        esac
                        breaks "$consumer" "$(django_document "$consumer" "$tag")" "$mine" "$LEVELS"
                        record $?
                    done
                done ;;
            *) echo "api-contract: unknown kind '$kind'" >&2; exit 2 ;;
        esac
    done
    [ "$status" -ne 1 ] || pr_fix "$kind"
    exit "$status"
}

case ${1:-} in
    deploy) shift; [ $# -ge 4 ] || { echo "usage: api-contract.sh deploy DJANGO_LIVE DJANGO_TARGET STOREFRONT_LIVE STOREFRONT_TARGET [GATEWAY_LIVE GATEWAY_TARGET]" >&2; exit 2; }; deploy "$@" ;;
    pr) shift; [ $# -ge 3 ] || { echo "usage: api-contract.sh pr KIND REPO_ROOT ENVIRONMENT..." >&2; exit 2; }; pr "$@" ;;
    *) echo "usage: api-contract.sh deploy|pr ..." >&2; exit 2 ;;
esac
