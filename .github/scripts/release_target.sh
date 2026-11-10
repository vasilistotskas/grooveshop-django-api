#!/usr/bin/env bash
# Decides what the release job does with the commit its run tested, when
# main may have moved since. Writes ``release=true|false`` and ``ref=<sha
# to release from>`` to $GITHUB_OUTPUT (to stdout when that is unset).
# ``::warning::`` and ``::error::`` go to stdout, where the runner reads
# workflow commands; the plain reason goes to stderr.
#
#   release_target.sh <tested sha>
#
# Runs on main are never cancelled, so a run can reach its release job
# with main ahead of the commit it tested. semantic-release pushes its
# version commit with a plain ``git push <remote> main``
# (``semantic_release/gitproject.py``), which the remote refuses unless
# it is a fast-forward, so it can only release from the tip. It reads
# every commit since the last release tag
# (``semantic_release/version/algorithm.py``), so releasing from the tip
# releases the tested commit too.
#
# What is between the tested commit and the tip decides:
#
#   tip                                  | this job
#   -------------------------------------+--------------------------------
#   the tested commit                    | releases
#   only commits that start no run:      | releases FROM THE TIP. The tree
#   path-ignored files (docs), and the   | differs from the tested one only
#   bot's ``[skip ci]`` version commits  | in files the workflow ignores or
#   (its subject, its three files, and   | the bot writes; no later run
#   semantic-release as author and       | exists to do it
#   committer)                           |
#   a later code commit whose run is     | skips, with a warning naming the
#   queued, running or green             | run: that run tested a tree that
#                                        | holds this commit and releases
#                                        | both (two merges in a row; a
#                                        | version commit a concurrent run
#                                        | pushed sits behind such a commit)
#   a later code commit whose run failed | FAILS. Nothing is released until
#   or was cancelled, or whose release   | main is green; then re-run that
#   job was cancelled or failed          | run's release job
#   a later code commit with no run      | FAILS (a ``[skip ci]`` push of
#   (after two minutes of looking)       | code: untested, nothing to wait
#                                        | for)
#   not a descendant of the tested       | FAILS (main was rewritten)
#   commit                               |
#
# A release that does not happen is therefore either a warning that names
# the run responsible for it, or a red job. Never a silent green.
#
# ``IGNORED`` mirrors ``on.push.paths-ignore`` in ci.yml;
# tests/unit/core/test_release_target_ignores.py fails when the two differ.
#
# Needs a full clone, GH_TOKEN with actions read, GITHUB_REPOSITORY and
# GITHUB_WORKFLOW_REF.
set -euo pipefail

sha=$1
workflow_path=${GITHUB_WORKFLOW_REF#"$GITHUB_REPOSITORY"/}
workflow_path=${workflow_path%%@*}

IGNORED=('**.md' 'docs/**' 'LICENSE' '.editorconfig')
RELEASE_FILES=('pyproject.toml' 'uv.lock' 'CHANGELOG.md')
RELEASE_SUBJECT='^chore\(release\): release version [0-9]+\.[0-9]+\.[0-9]+ \[skip ci\]$'
# Who semantic-release commits as: its default ``commit_author``, which
# pyproject.toml does not override. The same test pins it.
RELEASE_BOT='semantic-release <semantic-release>'

output() {
  echo "$1" >> "${GITHUB_OUTPUT:-/dev/stdout}"
}

fail() {
  echo "::error::$1"
  exit 1
}

ignored_path() {
  # The four patterns as GitHub reads them: ``**.md`` and ``docs/**``
  # cross directories, the other two are exact.
  case $1 in
    *.md | docs/* | LICENSE | .editorconfig) return 0 ;;
  esac
  return 1
}

release_file() {
  local file
  for file in "${RELEASE_FILES[@]}"; do
    [[ $1 == "$file" ]] && return 0
  done
  return 1
}

# True when the commit started no run of this workflow and cannot have
# changed behaviour: only ignored paths, or the bot's version commit.
starts_no_run() {
  local commit=$1 file is_release=false
  if [[ $(git log -1 --format=%s "$commit") =~ $RELEASE_SUBJECT ]]     && [[ $(git log -1 --format='%an <%ae>' "$commit") == "$RELEASE_BOT" ]]     && [[ $(git log -1 --format='%cn <%ce>' "$commit") == "$RELEASE_BOT" ]]; then
    is_release=true
  fi
  while IFS= read -r file; do
    [[ -z $file ]] && continue
    ignored_path "$file" && continue
    $is_release && release_file "$file" && continue
    return 1
  # ``--no-renames``: a file moved into an ignored path is listed under
  # its old path too, the one that made GitHub start a run.
  done < <(git diff --no-renames --name-only "$commit^1" "$commit")
  return 0
}

git fetch --quiet origin main
tip=$(git rev-parse FETCH_HEAD)

if [[ $tip == "$sha" ]]; then
  echo "the tested commit is the tip of main" >&2
  output "release=true"
  output "ref=$sha"
  exit 0
fi

git merge-base --is-ancestor "$sha" "$tip" \
  || fail "main ($tip) does not descend from the tested commit $sha"

newest_code=""
while IFS= read -r commit; do
  starts_no_run "$commit" || newest_code=$commit
done < <(git rev-list --reverse --first-parent "$sha..$tip")

if [[ -z $newest_code ]]; then
  # Every commit since started no run. Checked again on the whole range,
  # which is what the release will actually contain.
  while IFS= read -r file; do
    [[ -z $file ]] && continue
    ignored_path "$file" || release_file "$file" \
      || fail "$file changed between $sha and $tip without a run"
  done < <(git diff --no-renames --name-only "$sha" "$tip")
  echo "main moved to $tip by commits that start no run; releasing from it" >&2
  output "release=true"
  output "ref=$tip"
  exit 0
fi

# A later code commit: its own run has to do the release.
run=""
for _ in $(seq 1 "${RELEASE_TARGET_ATTEMPTS:-12}"); do
  run=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs?head_sha=$newest_code&event=push" \
    --jq "[.workflow_runs[] | select((.path | split(\"@\")[0]) == \"$workflow_path\")][0] | select(. != null) | \"\(.status) \(.conclusion // \"none\") \(.id) \(.html_url)\"")
  [[ -n $run ]] && break
  sleep "${RELEASE_TARGET_WAIT:-10}"
done
[[ -n $run ]] \
  || fail "main moved to $tip; commit $newest_code changed code but has no $workflow_path run, so nothing will release $sha"

read -r status conclusion run_id url <<<"$run"
if [[ $status != completed ]]; then
  # A run still going whose release job is already over cannot release
  # any more: GitHub cancels a waiting release job when a newer one
  # queues (``concurrency: release``), and the run stays "in progress"
  # for a moment after.
  release_job=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$run_id/jobs?per_page=100"     --jq '[.jobs[] | select(.name == "release")][0] | select(. != null) | (.conclusion // "none")')
  case $release_job in
    cancelled | failure | timed_out)
      fail "main moved on, and the release job of the run that has to release $sha ended $release_job: $url. Re-run that job."
      ;;
  esac
fi
if [[ $status != completed || $conclusion == success ]]; then
  echo "::warning::Not released from this run: main moved on, and $url (commit $newest_code, $status) tested a tree that holds this commit and releases it too."
  output "release=false"
  output "ref=$sha"
  exit 0
fi
fail "main moved on, and the run that has to release $sha ended $conclusion: $url. Nothing is released until main is green; then re-run its release job."
