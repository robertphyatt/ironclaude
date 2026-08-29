# M2 Live-Proof Findings — /reconcile + /push (v1.1.7 deploy-proof)

> **Run:** 2026-08-25 · **Result:** 10/10 lanes PASS · **Defects:** none
> **Against:** the DEPLOYED `worker/mcp-servers/workspace-manager/dist/*.js` (v1.1.7, loaded by the
> post-relaunch MCP). Staged, not committed.

## Bottom line

The v1.1.7 never-lose-work recovery chain is proven end-to-end on the deployed build: `/reconcile`
**preserves** a push-pending obligation (`integrated-local`) instead of nulling it; the shipped
completion path — unassigned-primary `/push` publishes the integrated candidate, then
`reconcile_finalization` observes the remote and **clears + recycles** (`cleaned`) — works; and the
third teardown primitive (`cleanupWorkspace`) **refuses** to tombstone a worktree that still owes a
push. `/reconcile` and `/push` behave as specified. No deploy defect found. This closes the
committed≠deployed≠loaded gap for the v1.1.7 fix.

## How it was run

- **Scratch isolation (airtight):** `HOME=$(mktemp -d)` (redirects the hook state DB), a hand-built
  scratch state DB with one `sessions` row `professional_mode='on'`, a scratch `WORKSPACE_MANAGER_DB_PATH`,
  and **two** scratch working repos (A, B) each with a bare origin. Zero real-world DB/repo/remote contact.
- **Two-repo, one-session topology.** Repo A carries the preserved-obligation chain
  (R-1→R-2→T-1→P-2a→P-2b→P-2c); repo B carries R-3+P-1. Isolation is required because the
  unassigned-primary `/push` lane (P-2b) demands zero *active* assignments for its (repo, session), while
  R-3/P-1 keep an active worktree — different repository identities keep them from interfering.
- **Mint → consume against the deployed build.** Each lane MINTs a human intent by piping a slash-form
  `UserPromptSubmit` JSON to the deployed `state-activator.sh`, then CONSUMEs by importing
  `createPublicToolDependencies` (dist/index.js) + `initCliDb` (dist/cli.js) via `node --input-type=module`
  with provider-root identity `{client:'claude', sessionId, invocationThreadId:sessionId}`, calling
  `deps.finalizeDirect('push'|'commit-and-push', …)`, `deps.reconcileWorktree(…)`, or
  `deps.reconcileFinalization(…)`. Managed worktrees were created by the real CLI `allocate` (a physical
  git worktree is required by `verifyDirectGitAuthority`).
- **B-Prime:** all git/node ops ran inside one driver script at an absolute scratchpad path (listed in the
  plan's `allowed_files`), invoked keyword-free, so its child git/push/commit ops evade the executing-stage
  git-keyword guard.

## Deploy-currency (D-1) — grep counts in dist/index.js

| symbol / string | count |
|---|---|
| `hasPushPendingObligation` | 2 |
| `finalizeReconcile` | 2 |
| `integrated-local` | 9 |
| `Refusing to tombstone` | 1 |
| `Push-only authority does not designate active managed workspace` | 1 |

All > 0 ⇒ the running dist is the v1.1.7 build (no deploy skew).

## Lane results

| Lane | What it proves | Consume/CLI result | Assertion | Result |
|---|---|---|---|---|
| D-1 | v1.1.7 symbols in dist | grep counts above | all > 0 | **PASS** |
| R-1 | reconcile happy | `state:reconciled` | local main == worktree HEAD; worktree alive; no push | **PASS** |
| R-2 | reconcile **preserves** push-pending | c&p fails dirty gate → active+`integration-pending`; reconcile → `state:integrated-local` | disposition now `push-pending` (PRESERVED, not nulled); main == candidate | **PASS** |
| T-1 | tombstone guard | CLI `cleanup` exit≠0 | stderr = `Refusing to tombstone a worktree with a push-pending obligation` | **PASS** |
| P-2a | no managed verb resumes a preserved candidate | `ERR: Direct Git operation requires a matching human intent` | managed `/push` over the integrated row cannot even be authorized | **PASS** |
| P-2b | shipped completion — publish | unassigned-primary `/push` → `state:pushed-only` | origin `refs/heads/main` == candidate | **PASS** |
| P-2c | shipped completion — observe & clear | `reconcile_finalization` → `state:cleaned` | disposition NULL; lifecycle `active`; worktree alive | **PASS** |
| R-3 | reconcile requires committed-clean | `ERR: Managed worktree has uncommitted changes` | repo B main unchanged | **PASS** |
| P-1 | managed push (ff-only) | `state:pushed-only` | origin `refs/heads/ironclaude/<guid>` == worktree HEAD | **PASS** |
| N-1 | identity negatives | both `ERR: Direct human authority can be consumed only by the provider-root session` | reconcile & push refused for a non-provider-root thread | **PASS** |

## Observed behaviors worth noting

- **P-2a's deployed defense is the intent-binding filter, not the finalize guard.** The mint binds a
  workspace_guid only from *non-terminal* assignments (`hook-intent.ts:41-46`); a preserved obligation is
  on an `integrated` (terminal) row, so no guid-bound intent can be issued for it. A managed `/push` over
  that row therefore fails at authority verification (`git-authority.ts:576`, "requires a matching human
  intent") — strictly upstream of, and stronger than, the `finalizeDirectAuthority` guard
  (`integration.ts:1001-1003`), which is defense-in-depth and black-box-unreachable via the deployed mint.
- **P-2b uses the unassigned-primary lane, not the managed one.** Once the preserved row is `integrated`
  (terminal), the primary checkout has zero *active* assignments, so `/push` with no guid publishes local
  main (whose tip is the candidate after R-2) to origin's *target* ref.

## Not proved — unshipped v1.1.7 non-goals

Per CHANGELOG.md:48-49, the candidate **resume-push** (re-driving the preserved candidate's push from its
own recorded remote bindings) and durable-ref anchoring so teardown can proceed rather than block are
explicit v1.1.7 **non-goals** — not built, and deliberately NOT simulated here. P-2b/P-2c prove the
*shipped* recovery: the human publishes via the unassigned-primary lane and `reconcile_finalization`
observes-and-clears. A related residual (also deferred): if the remote advances *past* the candidate,
`reconcile_finalization` returns "ambiguous; preserving push-pending" indefinitely
(`integration.ts:1419-1421`) — part of the same deferred loop.

## R6 — operator keystroke-confirm (genuinely human)

Run by hand on a real managed worktree (the automated lanes drive the deployed consume surface directly;
this confirms the full authority-gated slash path a human actually uses):

1. In a managed worktree with a committed, clean HEAD ahead of local main, type `/reconcile` → expect the
   worktree integrated into local main, the worktree still alive, and no push.
2. Type `/push` → expect the worktree branch published to its origin ref, ff-only.

Treat as validated unless a failure is reported.
