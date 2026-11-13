# API contract

The one rule that decides whether this API and its consumers (the storefront,
including its allauth parsers, and the agent gateway) can be live at the same
time, and the tooling around it. Django defines the API, so the rule lives
here, next to `schema.yml` and `allauth-schema.yml`.

| File | What it is |
|---|---|
| `api-contract.sh` | The single script. `deploy ...` is what the deploy gate in `grooveshop-infrastructure` runs; `pr KIND ROOT ENVIRONMENT...` is what a pull request check runs. Always inside the pinned oasdiff image. |
| `storefront-levels.txt` | oasdiff severity overrides for the storefront pairing. |
| `ignore.txt` | Breaks accepted on purpose, one line each, with an `until` release. |
| `check-ignore.py` | Fails an entry whose `until` release is live in production, so overrides cannot pile up. |
| `live_state.py` | Reads what is live per environment from GitHub Deployments. |
| `services.txt` | The repositories, named once. |
| `allauth-required.txt` | `true` once the storefront commits `openapi/allauth-schema.yml`: a missing allauth document then fails the check instead of being reported as not checked. |
| `watched.txt` | The files whose change makes a pull request check run. |
| `oasdiff-image.txt` | The oasdiff image, by tag and digest. The single pin; the deploy gate's literal image in `grooveshop-infrastructure` must equal it. |
| `tests/` | Unit tests, and `replay.sh`, which replays the 2026-11-03 deadlock against the real released documents. |

## What is live

The cluster reconciler records each service release that finished rolling out
as a GitHub Deployment of the service's own repository (`environment`
`production` or `staging`, `ref` the release tag, status `success`).
`live_state.py` takes the newest success per environment, plus any newer
deployment still rolling out. A rollback is a newer deployment of the older
tag. No record fails the check: it never passes on an unanswered question.

### When the recorder stops

The records carry no age. If the recorder stops, the last `success` record
of each environment stays readable and the checks keep passing or failing
against that last recorded release: a stalled recorder can neither block
every pull request nor silently turn them all green. Only two things fail
closed (exit 1, "cannot say what is live"): an environment with no
successful record at all, and GitHub's API not answering. What a stopped
recorder can do is let a check run against a release that has since been
replaced; the deploy gate still checks the real pods, and
`GrooveShopDeploymentRecordsStale` fires. The runbook is "Recorder stalled"
in `grooveshop-infrastructure/docs/deploying.md`.

## The direction of a verdict

`oasdiff breaking CONSUMER DJANGO` reports what in Django breaks a client of
the consumer's document.

- A Django pull request is checked against the storefront and gateway live
  in production and in staging. A removal, loosening, new enum value or new
  success status ships consumer first.
- A storefront or gateway pull request is checked against the Django live
  in production and staging. A new requirement ships Django first.

A pull request that is wrong in either direction cannot merge; it has to be
split. A change that has both kinds (a required field and an enum value) can
never pass in one piece.

## An accepted break

Only a break with no safe order is accepted, in one of the two cases below.
Add the line oasdiff printed to `ignore.txt` in the pull request that makes
the change:

```
<date> until <service> <vX.Y.Z> | <reason and who decided> | <oasdiff line>
```

`until` is the first release of that consumer that no longer needs it. Once
production runs it, `check-ignore.py` fails the line, and deleting it is
proved safe by the same check.

**A route nothing calls.** No consumer sends a request to it, so no order
can break one.

**A declaration that never matched the body.** The schema declared a response
field the server never sent (a comment's parent author declared as the full
account while the body carried the public author). The consumer generates its
checks from that document, so it requires something no response ever had. The
pull request must show all of this:

- The body is unchanged: a test that validates real responses against the
  schema fails before the declaration is corrected and passes after, and a
  real response compared before and after is identical.
- Every order is refused by the gate, the consumer-first one included. The
  consumer regenerates its checks from this document, so it can only stop
  requiring the field once Django no longer declares it. A transitional
  declaration that offers both shapes is refused as well
  (`response-property-one-of-added`) and publishes a shape the server never
  sends.
- The fields the consumer requires must never be sent (another reader's
  private data, say), so splitting the change cannot satisfy it.

`until` then names the consumer release regenerated from the corrected
document.

A break that only exists because a consumer requires something Django does not
provide yet is never accepted: split the change. The two differ in time. "Not
yet" is something Django will send once its side lands, so the change is split
by order: Django ships the field first, then the consumer that requires it (a
new requirement ships Django first, as above). "Must never" is something Django does
not send and will not, so no order and no split can meet it; only a corrected
document can.

## Run it locally

```bash
python3 -I -m unittest discover -s api-contract/tests   # unit tests
bash api-contract/tests/replay.sh                       # #442 / #347 replay (docker, network)
```
