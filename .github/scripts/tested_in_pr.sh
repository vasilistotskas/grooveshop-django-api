#!/usr/bin/env bash
# Prints ``tested=true`` when CI already passed on exactly the code a push
# to main carries, else ``tested=false``; the reason goes to stderr.
#
#   tested_in_pr.sh <pushed sha> <previous main tip>
#
# A squash merge commits the PR's tree onto the previous tip of main. When
# the PR head already contained that tip, the merged tree equals the head's
# tree, and the PR's run tested that very tree: its merge ref (base + head)
# is the head itself, since main only moves forward and the base it saw is
# an ancestor of the previous tip. So the push is already tested when all
# of these hold:
#   1. the sha is the merge commit of a merged PR;
#   2. the previous tip is an ancestor of (or equal to) the PR head;
#   3. the sha and the PR head have the same tree;
#   4. this workflow succeeded on a pull_request run for the PR head, and
#      the jobs that test code succeeded in it (not skipped: a pull request
#      that touches only ignored paths runs green without the tests).
# Anything else (a branch behind main, a direct push, a dispatch) is
# "not tested", and the workflow runs in full.
#
# Needs GH_TOKEN with contents, pull-requests and actions read, plus the
# runner's GITHUB_REPOSITORY and GITHUB_WORKFLOW_REF
# (owner/repo/.github/workflows/<file>.yml@<ref>).
set -euo pipefail

sha=$1
before=$2
workflow_path=${GITHUB_WORKFLOW_REF#"$GITHUB_REPOSITORY"/}
workflow_path=${workflow_path%%@*}

untested() {
  echo "not tested: $1" >&2
  echo "tested=false"
  exit 0
}

[[ $before =~ ^[0-9a-f]{40}$ && $before != 0000000000000000000000000000000000000000 ]] \
  || untested "no previous tip of main"

head=$(gh api "repos/$GITHUB_REPOSITORY/commits/$sha/pulls" \
  --jq ".[] | select(.merge_commit_sha == \"$sha\") | .head.sha")
[[ -n $head ]] || untested "$sha is no PR's merge commit"

status=$(gh api "repos/$GITHUB_REPOSITORY/compare/$before...$head" --jq .status)
[[ $status == ahead || $status == identical ]] \
  || untested "PR head $head is $status of the previous tip $before"

tree() { gh api "repos/$GITHUB_REPOSITORY/commits/$1" --jq .commit.tree.sha; }
[[ $(tree "$sha") == $(tree "$head") ]] \
  || untested "the merged tree differs from PR head $head"

# A green run is not enough: a pull request that changes only ignored paths
# runs this workflow green with every test job skipped. The run counts only
# when the jobs that test code ran and succeeded in its LATEST attempt (a job
# cancelled and then re-run counts by its re-run). ``skipped`` is not enough.
# Any API error aborts the script (``set -e``): no output, so ``tested`` is
# unset, and the workflow runs everything.
#
# ``REQUIRED_JOBS`` is the names of the jobs in the gate's ``needs`` that
# test code; tests/unit/core/test_release_target_ignores.py fails when it
# differs from ci.yml. ``Coverage Gate`` needs every ``Testing`` shard, so it
# is green only when they all were.
REQUIRED_JOBS=('Security Scan (Trivy)' 'Code Quality' 'Migration Check' 'Testing (multi-tenant lane)' 'Coverage Gate')

read -r run_id run_url < <(gh api "repos/$GITHUB_REPOSITORY/actions/runs?head_sha=$head&event=pull_request&status=success&per_page=100" \
  --jq "[.workflow_runs[] | select((.path | split(\"@\")[0]) == \"$workflow_path\")][0] | select(. != null) | \"\(.id) \(.html_url)\"")
[[ -n ${run_id:-} ]] || untested "no successful $workflow_path run for PR head $head"

# One ``name<TAB>conclusion`` line per job of the latest attempt.
jobs=$(gh api --paginate "repos/$GITHUB_REPOSITORY/actions/runs/$run_id/jobs?filter=latest&per_page=100" \
  --jq '.jobs[] | "\(.name)\t\(.conclusion)"')
for job in "${REQUIRED_JOBS[@]}"; do
  grep -qxF "$job"$'\t'"success" <<<"$jobs" \
    || untested "run $run_url did not run $job to success (a docs-only run skips the tests)"
done

echo "tested in $run_url (PR head $head, same tree, tests ran)" >&2
echo "tested=true"
