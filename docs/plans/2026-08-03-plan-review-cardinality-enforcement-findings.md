# Plan-review cardinality enforcement — verification findings

## Invariant and root cause

Each plan lineage has at most one blind review (`SOLID`, `HAS-ISSUES`, or
`top-tier-self`).  The prior persistence seam accepted repeated blind reviews
for one session because reviews had no lineage identity or uniqueness guard.
The completed change stores `plan_lineage` on sessions and review rows, advances
it only for a real transition into `design_ready`, rejects duplicate blind
review insertion before audit mutation, and installs the same constraint as a
SQLite `BEFORE INSERT` trigger.

## Migration and historical rows

The migration suite passed with duplicate historical review rows preserved.
The canonical historical blind review is selected by earliest `id` in lineage
zero.  Migration tests cover both historical orderings (`HAS-ISSUES` first and
passing verdict first), raw-SQL trigger rejection, and trigger persistence after
database reopen.  Fresh-schema tests separately verify trigger installation,
first insert acceptance, second-insert rejection, and rejection after reopen.

## RED and GREEN evidence

Recorded RED evidence before implementation:

- Task 1 lineage/migration suite: four failing assertions; bootstrap hook RED.
- Task 1 concurrency probe: a session at lineage 41 was overwritten to 1.
- Task 2 review repair: 178 of 179 full-suite tests passed; one failed because
  a shared fixture omitted `tier_up_reviews.plan_lineage`.

GREEN verification completed after the final bundle build:

| Command | Result |
| --- | --- |
| `npm --prefix worker/mcp-servers/state-manager run build` | Passed; TypeScript and esbuild rebuilt `dist/index.js` (548.7 kb). |
| Focused tier-up, transition, and migration tests | 3 files, 67 passed. |
| Read-tool dispatch test | 1 file, 6 passed. |
| `commander/.venv/bin/python -m pytest commander/tests/test_executing_plans_skill.py -q` | 17 passed. |
| Full state-manager test suite | 13 files, 182 passed. |
| `test-session-init-plan-lineage.sh` | Passed: bootstrap creates both lineage columns with default 0. |
| `test-workflow-transition-idempotency.sh` | 44 pass, 0 fail. |

The migration and fresh-database tests are the trigger/reopen race-backstop
evidence; they insert through raw SQL, reject a second blind review, close the
database, reopen it, and reject another second insert.

## Source and bundle parity

Independent source, read-tool, bootstrap, and bundle searches found the
`plan_lineage` schema/enforcement markers and the duplicate-review trigger
message.  Independent stale-guidance searches of `write-tools.ts` and the
bundle each returned exit 1 with no output for `fresh SOLID review`, `fresh
blind review`, and `re-review is required`.

The original broad skill probe for `dispatch a second` false-positively matched
the required prohibition, “Do not dispatch a second one.”  A refined probe for
only affirmative stale instructions (`obtain a fresh SOLID review`, `obtain a
fresh blind review`, `re-review is required`, `perform a second blind review`,
or `run a second blind review`) returned exit 1 with no output.  No skill file
was changed in Task 3.

## Scope

Task 3 rebuilt only `worker/mcp-servers/state-manager/dist/index.js` and added
this findings artifact.  Commander, Slack, provider handling, and code-review
frequency were unchanged.  Only approved plan outputs were staged; no commit
was created and no push occurred.
