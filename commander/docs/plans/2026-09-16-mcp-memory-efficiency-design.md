# Concurrent IronClaude Memory-Efficiency Fixes — Design

> **Created:** 2026-09-16
> **Status:** Design Complete
> **Scope mode:** selective → operator chose ALL EIGHT fixes in one loop.
> **Source:** investigation findings `2026-09-16-mcp-teardown-findings.md` + Fable
> consult (all code claims verified against source this session).

## Summary

Make IronClaude reliable and memory-efficient under the operator's target
workload: many concurrent operator-driven Claude sessions + the Commander daemon
(Brain + tmux workers) on a 48 GB Mac. The investigation proved there is no MCP
leak today, but (a) the daemon is blind to the actual OOM condition (its spawn gate
and heartbeat track no swap/pressure), (b) finished workers are left alive holding
~1.5-2 GB each, (c) MCP servers have no self-exit so a signal-less death *can*
orphan a 6-proc tree, and (d) several smaller RAM/correctness items. This loop
implements all eight fixes, structured in dependency waves, with the two
architecturally heavier items (embedding offload, wrapper collapse) designed
**safe-by-default** so they land without degrading behaviour.

## Principle / non-goals

- The dominant RAM term is live `claude`/`codex` root count × lifetime, NOT the MCP
  fork. Fixes are prioritised accordingly: bound + self-clean root count first,
  measure everything, then reclaim the smaller MCP-side terms.
- **NEVER pattern-kill** — every teardown scopes to a recorded PID (pgrep stays
  log-only where kept). **No work loss** — every worker kill runs the integration
  seam first. **No behaviour change by default** for the embedding backend.
- Out of scope: version bump / CHANGELOG / commit / push (operator-gated
  separately). Reducing the *number* of concurrent sessions is operator behaviour,
  not code.

## Components (→ waves)

### Component A — Daemon memory-visibility (fix 0) [Wave 1]
**A1 — pressure-aware spawn gate.** `orchestrator_mcp.py` `_check_spawn_preconditions`
(gate at ~:2760-2782). KEEP the deliberate 10%-of-total `available_gb` floor
(directive #1030). ADD, after it, a swap/pressure guard: reject spawn when
`psutil.swap_memory().used` exceeds `max_swap_used_gb` (new config, default 8.0) OR
macOS pressure is non-normal (`sysctl -n kern.memorystatus_vm_pressure_level`:
1=normal, 2=warn, 4=critical → reject on ≥2, config `spawn_block_on_pressure`
default true). Fail-open on a sysctl/psutil error (log at INFO, do not block) so a
probe failure never wedges spawning. New config keys added to
`config/ironclaude.json.example` with documented defaults.
**A2 — heartbeat memory line.** `main.py` `format_heartbeat`: add one line —
`mem: <available_gb> free / swap <used_gb> / pressure <normal|warn|critical>` — plus
the top-3 RSS consumers (a small psutil helper in main.py; do NOT depend on the
orchestrator MCP tool from the daemon loop). Surfaced in the daemon heartbeat that
already posts to Slack.

### Component B — Idle-worker TTL (fix 1) [Wave 2, depends A]
`main.py` `check_workers` done-marker path (~:4211-4241). The idle path today keeps
the worker alive and re-notifies the Brain each cycle (intentional — Brain reuses
workers). ADD a conservative TTL: track `_worker_idle_since[worker_id]` set when the
done-marker first appears; RESET (delete) it whenever the worker becomes non-idle
(marker gone, or a new prompt/activity). When a worker has been continuously idle
for `idle_worker_ttl_seconds` (new config, default 1800 = 30 min), escalate: call
`_finalize_and_release_worker(worker_id, reason="idle-ttl", terminal=True)` (the
existing seam — integrates/rescues reviewed work with gate checks, NEVER loses
work), then the seam's terminal path + the existing session-died handling reclaim
the tmux session (clean SIGHUP → pane group → the 6 MCP procs die). Guardrails: the
TTL never fires while the Brain has sent the worker work this cycle; a 0/negative
config disables it; the reset-on-activity guarantees a reused worker's timer
restarts. Surface each TTL reap to Slack once.

### Component C — MCP server self-exit on parent death (fix 2) [Wave 1, parallel]
All three servers, episodic-memory first (zero PPID handling today). At each
transport-connect entrypoint (workspace-manager `src/index.ts:582`, state-manager
`src/index.ts:180`, episodic-memory `src/mcp-server.ts:332`) add:
`process.stdin.on('end', () => process.exit(0))` AND a `CLAUDE_PPID` liveness poll —
`const ppid = Number(process.env.CLAUDE_PPID); if (ppid) setInterval(() => { try {
process.kill(ppid, 0) } catch (e) { if (e.code === 'ESRCH') process.exit(0) } },
30000).unref?.()` — NOTE: the poll interval is deliberately **refed** (do NOT
`.unref()`) because it IS the watchdog that must keep running; only stdin-EOF or a
dead parent may exit the process. Works for both clients (the wrapper sets
`CLAUDE_PPID` for Claude and Codex alike). Rebuild the three `dist/` bundles.

### Component D — Dead-code + pattern-kill cleanup (fix 6) [Wave 1, parallel]
**D1 —** delete `_cleanup_zombie_mcp_processes` (`orchestrator_mcp.py:6706-6749`,
never fires on macOS: `os.kill(ppid,0)` on a launchd-reparented orphan always
succeeds → skip), its `_MCP_CLEANUP_PATTERNS` constant, and its call site in
`restart_mcp`. **D2 —** `_kill_orphan_brains` (`main.py`): keep it as the
belt-and-suspenders fallback it is (memory: it runs after structured
`brain.shutdown()` + `pkill -P`), but target the **recorded brain PID** primarily
(`_daemon.brain._brain_pid`, else `/tmp/ic/brain.pid`); keep the `pgrep -f` result as
LOG-ONLY verification, never the kill selector, per the never-pattern-kill directive.

### Component E — vitest fan-out caps (fix 5) [Wave 1, parallel]
Add `test: { pool: 'forks', poolOptions: { forks: { maxForks: 2 } } }` (or
`maxWorkers: 2`, matching the installed vitest major) to the three packages'
configs: `workspace-manager/vitest.config.ts` (extend existing), and NEW minimal
`vitest.config.ts` for state-manager and episodic-memory. Removes multi-GB
test-burst spikes when suites run concurrently.

### Component F — Embedding offload (fix 3) [Wave 3, SAFE-BY-DEFAULT]
episodic-memory `src/embeddings.ts` pins `Xenova/all-MiniLM-L6-v2` in a module
singleton (~0.3-0.4 GB per session, never released). Make the embedder a
**configurable backend** with **local as the default** (zero behaviour change unless
opted in): a `getEmbedding()` that dispatches on `IC_EMBEDDING_BACKEND` env/config —
`local` (current Xenova pipeline, default) or `ollama` (POST to an Ollama
`/api/embeddings` endpoint, model `all-minilm`, base URL from the shared
hooks-config / an env var). The `ollama` path has a **hard fallback to local** on any
network error (amd-halo has had outages) so search never breaks. **Re-index caveat
documented, not automated:** Xenova and Ollama MiniLM embeddings are the same family
(384-dim) but not bit-identical; mixing them degrades similarity search, so flipping
to `ollama` requires a one-time re-index — provided as a documented operator step
(a `reindex` npm script), NOT run automatically. The RAM win is realised when the
operator flips the config and re-indexes; the code lands safely now.

### Component G — Wrapper collapse (fix 4) [Wave 4, LAST, careful]
Collapse the fork to in-process load. Node has no `execvp`; the clean form in each
`cli/mcp-server-wrapper.js` is: run `ensureRuntime()` (build-on-first-run, unchanged),
set `process.env.CLAUDE_PPID = String(process.ppid)` BEFORE loading, then
`await import(bundle)` in the SAME process instead of `spawn`. MUST preserve: the
npm-chatter isolation (`ensureRuntime`'s `execFileSync` with piped stdout runs before
the import, so no npm output reaches the stdio transport), and each wrapper's
`process.on('exit')` cleanup (e.g. state-manager's PENDING_MARKER unlink) which
survives as same-process. `process.argv[1]`/`import.meta.url` entry checks in the
bundles must be re-verified (argv[1] becomes the wrapper path) — grep each bundle
first. Halves node MCP proc count (removes the 3 child procs/session). Rebuild
bundles. Test against BOTH plugin caches (Claude 1.1.x + Codex 1.1.11+codex) since
the census showed both launch this wrapper.

## Data Flow / Error Handling

- A1/A2 read-only telemetry; A1 fails OPEN (probe error → do not block spawn).
- B integrates before kill (no work loss); disabled by a 0 TTL; reset-on-activity
  prevents killing a reused worker.
- C: stdin-EOF exit + refed PPID-poll watchdog; both needed (EOF alone insufficient
  if a descendant holds the pipe write end; poll alone misses a clean stdin close).
- D2 fails safe: recorded PID missing → log and fall back to the existing structured
  path; never pattern-kills.
- F: `ollama` backend → local fallback on any error; default stays local.
- G: any import/entry-check failure surfaces at server start (loud), tested on both
  caches before landing.

## Testing Strategy

Each executable change is TDD (RED→GREEN). Python: pytest for A1 (mock
swap/pressure → assert reject/pass + fail-open), A2 (heartbeat contains the mem
line), B (idle-since set/reset/TTL-fires-terminal-finalize via a fake clock + seam
mock; assert never fires with fresh activity), D1 (call site removed; function
gone), D2 (recorded-PID targeted; pgrep not used as selector). TS/vitest: C (stdin
'end' → exit(0); PPID poll → exit on ESRCH via a mocked process.kill), F (backend
dispatch: local default unchanged; ollama path + fallback-to-local on error), plus
the maxForks config (E). Terminal regression: full `pytest tests/` (commander),
`npm run build` + `npm test` for all three MCP packages, and a manual both-caches
smoke for G. Version consistency test stays green (no manifest change this loop).

## Implementation Notes

- Files: `commander/src/ironclaude/orchestrator_mcp.py` (A1, D1), `commander/src/ironclaude/main.py`
  (A2, B, D2), `config/ironclaude.json.example` (+ new keys), commander tests;
  `worker/mcp-servers/{episodic-memory,state-manager,workspace-manager}/src/…` (C, F,
  G src), their `cli/mcp-server-wrapper.js` (G), `vitest.config.ts` ×3 (E), rebuilt
  `dist/` bundles ×3, TS `__tests__`.
- **Waves:** W1 = A, C, D, E (independent). W2 = B (depends A's config plumbing). W3 =
  F. W4 = G (last — highest risk, touches the launch path both clients use).
- **High blast radius** (spawn gate, worker lifecycle, dual-client MCP launch,
  embedding backend) → tier-up (Fable) blind plan review AND tier-up adversarial end
  review over the staged diff.
- **Commit-sequencing surface (operator decision, at commit time):** the working tree
  already holds 13 staged boy-scout v1.1.11 files + 4 investigation docs, and this
  loop edits `main.py`/`orchestrator_mcp.py` on top of the boy-scout `main.py` edits.
  These cannot be separated by file. At commit I will surface: either amend the
  boy-scout v1.1.11 first (clean base) then commit this loop separately, or accept
  one combined commit. Do NOT push (explicit go required, each time).
