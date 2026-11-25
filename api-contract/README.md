# API contract

The one rule that decides whether this API and its consumers (the storefront,
including its allauth parsers, and the agent gateway) can be live at the same
time, and the tooling around it. Django defines the API, so the rule lives
here, next to `schema.yml` and `allauth-schema.yml`.

| File | What it is |
|---|---|
| `api-contract.sh` | The single script. `deploy ...` is what the deploy gate in `grooveshop-infrastructure` runs; `pr KIND ROOT ENVIRONMENT...` is what a pull request check runs; `compare CONSUMER DJANGO [LEVELS]` pairs two documents by hand or in a test. Always inside the pinned oasdiff image. |
| `storefront-levels.txt` | oasdiff severity overrides for the storefront pairing. |
| `ignore.txt` | Breaks accepted on purpose, one line each, with an `until` release. |
| `check-ignore.py` | Fails an entry whose `until` release is live in production, so overrides cannot pile up. |
| `live_state.py` | Reads what is live per environment from GitHub Deployments. |
| `services.txt` | The repositories, named once. |
| `allauth-required.txt` | `true` once the storefront commits `openapi/allauth-schema.yml`: a missing allauth document then fails the check instead of being reported as not checked. |
| `watched.txt` | The files whose change makes a pull request check run. |
| `oasdiff-image.txt` | The oasdiff image, by tag and digest. The single pin; the deploy gate's literal image in `grooveshop-infrastructure` must equal it. |
| `tests/` | Unit tests; `compare.sh`, which pairs the documents in `tests/fixtures/` (no network); and `replay.sh`, which replays a recorded deadlock (a required field and an enum value in one change) against the real released documents. |

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

## Two passes per pairing

Every pairing runs oasdiff twice, and fails if either pass finds an error.

1. **Flattened** (`--flatten-allof`), with the consumer's levels and every
   check. drf-spectacular wraps every nested serializer in an `allOf`, and
   unflattened oasdiff reports a required property removed inside one only as
   a warning. Flattening is
   also what lets the two sides differ in shape: the gateway's contract,
   generated from Go types, has no `allOf`, and a consumer generated before
   Django wrapped a field has a plain `$ref` where Django now has `allOf`.
   Unflattened oasdiff cannot follow a property across that difference.
2. **Nullability, unflattened.** Flattening drops a `nullable: true` that sits
   beside an `allOf` ([oasdiff#1348](https://github.com/oasdiff/oasdiff/issues/1348)),
   and that is how drf-spectacular declares a nullable nested serializer. A
   required response object made nullable comes out of the first pass as "No
   changes detected". This pass runs only the `*-became-nullable` /
   `*-became-not-nullable` checks whose level for this consumer is ERR (its
   levels file, else oasdiff's default), written out as `err` so oasdiff does
   not lower one to a warning for sitting inside an `allOf`. The levels come
   from `oasdiff checks changelog` in the pinned image, so a pin bump needs no
   edit. A levels file that cannot be read, or a listing with no such check at
   ERR, fails the check instead of skipping it.

The second pass prints, under "nullability, compared unflattened", only what
the first did not: a finding the first pass printed, or one an `ignore.txt`
line accepts, is dropped. Both comparisons remove the
`allOf[#/components/schemas/X]/` segments the unflattened pass puts in a
property path, and match `ignore.txt` lines the way oasdiff does. So a change
both passes see is printed once, in the first pass's wording, and one accepted
line covers both passes. Copy the line as printed.

The second pass goes once the pinned oasdiff keeps `nullable` when it
flattens: `tests/compare.sh` then passes with the first pass alone.

## An accepted break

Only a break with no safe order is accepted, in one of the two cases below.
Add the line oasdiff printed to `ignore.txt` in the pull request that makes
the change:

```text
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

**A request field every live consumer already sends becomes required.**
The storefront's document is generated from this schema, so it declares
the field optional until Django requires it, and no order of releases
satisfies the check. The pull request must show, with file and line, that
the storefront and the gateway live in production and in staging send the
field on every request to the route (as for a required
`expectedTotal`). `until` names the storefront's next release, which
regenerates from this document.

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
bash api-contract/tests/compare.sh                      # fixture pairings (docker, no network)
bash api-contract/tests/replay.sh                       # recorded deadlock replay (docker, network)
```

Two documents by hand, in the pinned image (the consumer's first):

```bash
docker run --rm -v "$PWD:/w" -w /w --entrypoint /bin/sh "$(cat api-contract/oasdiff-image.txt)"   api-contract/api-contract.sh compare consumer.yml schema.yml
```
