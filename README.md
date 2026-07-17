> ⚠️ **History Rewrite Notice**
> This repository underwent a history rewrite on 2026-04-15. If you cloned before this date, your local copy is no longer compatible with `origin/main`.
> **Option A (recommended):** Delete your local clone and re-clone.
> **Option B:** `git fetch --all && git reset --hard origin/main` (discards any local changes).

# IronClaude

**Workflow discipline and multi-agent orchestration for Claude Code.**

IronClaude adds workflow discipline and multi-session orchestration to Claude Code. Without structure, sessions tend to jump to implementation without planning, produce tests that pass but miss regressions, and can't coordinate work across multiple sessions. IronClaude addresses this with two components that work together:

- **Worker** -- A Claude Code plugin that enforces disciplined development workflows (brainstorm, plan, execute) with review gates between every task
- **Commander** -- A Python daemon that orchestrates multiple Worker sessions via Slack, with an autonomous Brain that decomposes objectives and assigns work

The key insight: discipline and orchestration reinforce each other. The Worker's professional mode hooks guarantee that every autonomous session follows the full workflow -- so the Commander can trust the quality of work happening without your direct supervision. And because the Commander runs through Slack, you can supervise and direct multi-session autonomous work from your phone, from anywhere, without being at your terminal. The Brain acts as your proxy at the keyboard.

You can use the Worker alone for single-session discipline, or add the Commander for multi-session orchestration.

---

## Two Ways to Use IronClaude

**Start with direct mode. Graduate to commander mode.**

### Direct Mode — Recommended Starting Point

Install the Worker plugin into your own Claude Code session and use it yourself. You run the brainstorming, you approve the plans, you trigger execution, you read the code reviews. The state machine enforces the workflow on your session — you experience every phase transition, every review gate, every file access restriction firsthand.

This is where to start. The discipline isn't just for autonomous workers; it's for you. Working through the brainstorm → write-plans → execute-plans cycle yourself builds the intuition you need to be a good orchestrator later.

### Commander Mode — Advanced

Once you understand the workflow from the inside, use the Commander to spawn Workers that act as your avatar. Each Worker follows the exact same professional mode rules you learned in direct mode — the same three-phase workflow, the same code review gates, the same TDD discipline — but executes autonomously on your behalf while you supervise from Slack.

The Worker is your avatar at the keyboard. It does what you do in direct mode, just without you there. When you've internalized the rules yourself, you know what the Worker is doing and why, which makes you a more effective orchestrator: decomposing objectives, approving plans, reviewing outcomes, rather than writing every implementation yourself.

**Recommended progression:**
1. Install the Worker plugin into Claude Code (`/plugin marketplace add robertphyatt/ironclaude` — see [Quick Start](#quick-start-worker-claude-code-plugin) for details)
2. Activate professional mode: `/ironclaude:activate-professional-mode`
3. Work through several features using the full workflow — brainstorm, plan, execute, review
4. When the workflow feels natural, set up the Commander and begin delegating to Workers

**Both modes work together:** The recommended progression isn't "leave direct mode behind." Once you've set up the Commander, you'll use both simultaneously — personally running direct-mode sessions for exploratory or sensitive work while Workers handle parallelized or routine tasks. The real workflow isn't direct *or* Commander; it's direct *and* Commander, task by task.

---

## How IronClaude Compares

IronClaude was inspired by several projects in this space. Here's how it differs:

### vs. Superpowers

[Superpowers](https://github.com/obra/superpowers) by Jesse Vincent / Prime Radiant is the pioneering composable skills framework for Claude Code, with a 7-step workflow (brainstorm → git worktrees → write plans → execute → TDD → code review → finish branch) available in the Claude plugin marketplace. It has set the standard for disciplined Claude Code usage. IronClaude was directly inspired by Superpowers and uses its episodic memory MCP server (with attribution -- see [Episodic Memory Attribution](#episodic-memory-attribution)).

**Difference:** Superpowers guides the model through prompt-based discipline — a SessionStart hook injects the workflow skills into session context, and system prompts encourage compliance. IronClaude adds enforcement via PreToolUse/PostToolUse hooks: `professional-mode-guard.sh` blocks unauthorized tool usage at the hook level, and `skill-state-bridge.sh` enforces stage transitions. The workflow isn't just suggested, it's enforced. IronClaude also adds the Commander — multi-session orchestration via Slack with an autonomous Brain that acts as your proxy, decomposes objectives into parallel workstreams, and manages workers autonomously while you supervise from anywhere.

### vs. gStack

[gStack](https://github.com/garrytan/gstack) by Garry Tan provides 23 specialist roles (CEO, Designer, QA, etc.) and 31 total skills, with a Chrome controller and live activity feed.

**Difference:** gStack is role-based -- specialist personas for individual productivity. IronClaude is workflow-based -- a brainstorm-plan-execute pipeline with review gates, enforced by hooks that validate every action. gStack focuses on making one session more capable; IronClaude adds autonomous multi-session orchestration where discipline is enforced programmatically, not by persona instructions.

### vs. Ruflo

[Ruflo](https://github.com/ruvnet/ruflo) (formerly Claude Flow) is a multi-agent orchestration platform with 100+ specialized agents and swarm patterns (hierarchical, mesh).

**Difference:** Ruflo provides its own agent framework. IronClaude is lighter -- it orchestrates native Claude Code CLI sessions directly via tmux. Each worker is a real Claude Code session running under professional mode hooks, not a custom agent. No framework to learn; the discipline comes from the hooks and state machine, and the orchestration comes from the Commander managing real Claude Code sessions.

### vs. Claude Code Plan Mode

Claude Code has a built-in plan mode (`EnterPlanMode`/`ExitPlanMode`) that provides a 2-phase plan-then-execute workflow. It's advisory -- Claude can suggest skipping it, and there are no enforcement gates between planning and execution.

**Difference:** IronClaude replaces plan mode with a 3-phase workflow (brainstorm → write-plans → execute-plans) enforced by a hook-based state machine. Every action is validated against the current workflow stage, making discipline mandatory rather than suggested. Key additions over plan mode: review gates between every task, file access restrictions per task wave, MCP-backed state persistence across sessions, structured plan format (JSON + markdown), and wave-based parallel execution with dependency graphs. When professional mode is active, `EnterPlanMode` is blocked and redirected to the brainstorming skill.

---

## Quick Start: Worker (Claude Code Plugin)

The Worker is a Claude Code plugin that enforces the brainstorm-plan-execute workflow on every code change. Install it and activate professional mode to get disciplined single-session development.

### Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- Node.js 20+ (for MCP servers)
- sqlite3 CLI (for hooks — pre-installed on macOS; `sudo apt install sqlite3` on Linux)

### Install

```bash
# From within Claude Code:
/plugin marketplace add robertphyatt/ironclaude
/reload-plugins

# Security vulnerability detection (recommended):
/plugin install security-guidance@claude-plugins-official
/reload-plugins
```

> **First run:** The first time MCP servers start (~10-30s), they auto-build platform-specific binaries. Subsequent starts are instant. If startup fails, check `~/.claude/ironclaude-mcp-state-manager.log` and `~/.claude/ironclaude-mcp-episodic-memory.log`.

### Activate Professional Mode

```
/ironclaude:activate-professional-mode
```

When active, Claude operates in architect mode -- planning and designing without making code changes unless executing an approved plan. Every write action is validated by hooks that check whether it's permitted in the current workflow phase.

### Set Up Statusline (Recommended)

```
/statusline
```

Configures your terminal prompt to show IronClaude's current state: workflow stage, plan name, professional mode status, and review grades. Makes the workflow visible at a glance.

### The Workflow

```
You: "Add user authentication"
  |
Claude: [brainstorming] -- Design session with clarifying questions
  |
Claude: [writing-plans] -- Detailed implementation plan (2-5 min tasks)
  |
You: Approve plan
  |
Claude: [executing-plans] -- Task-by-task with code review between each
  |
Changes staged with git add (you commit manually)
```

### State Machine Stages

The workflow enforces transitions through named stages visible in the statusline:

| Stage | Meaning |
|-------|---------|
| `idle` | No active plan |
| `brainstorming` | Design session in progress |
| `debugging` | Systematic debugging investigation (0.75× staleness) |
| `design_ready` | Design written, awaiting plan creation |
| `design_marked_for_use` | Design registered for plan consumption |
| `plan_marked_for_use` | Plan pipeline — design consumed |
| `final_plan_prep` | Final validation before plan approval |
| `plan_ready` | Plan approved, awaiting execution start |
| `executing` | Task implementation in progress |
| `reviewing` | Code review in progress — `review_pending` blocks the next task |
| `plan_interrupted` | Mid-execution topic change detected |
| `execution_complete` | All tasks done |

Each phase transition is enforced by the state machine. Claude cannot skip brainstorming, cannot execute without an approved plan, and cannot move to the next task without passing code review.

### Validation Backend (Recommended)

Professional mode hooks use an LLM to validate actions in real time. The default backend (using Haiku) works out of the box, but you can use Ollama to run validation locally without adding to your Claude license usage:

```bash
# Install Ollama: https://ollama.com/download
ollama pull qwen3:8b
ollama serve

# In Claude Code:
/ironclaude:setup-ollama-validation
```

### Hook System

Enforcement is implemented in 16 hooks across 6 lifecycle events (SessionStart, PreToolUse, PostToolUse, UserPromptSubmit, SubagentStop, Stop). Key hooks:

| Hook | Trigger | Purpose |
|------|---------|---------|
| `professional-mode-guard.sh` | PreToolUse | Blocks write tools outside `executing` stage; allows a hardened read-only Bash allowlist (`cat`/`grep`/`find`/`ls`/… — no chaining, redirection, or `find` write actions) in every stage so research never requires write access; validates file access against the wave whitelist |
| `skill-state-bridge.sh` | PreToolUse | Detects Skill invocations; requests state machine transitions |
| `state-activator.sh` | UserPromptSubmit | Handles professional mode on/off; PPID-based session binding |
| `session-init.sh` | SessionStart | Initializes session, creates DB schema, configures statusline |
| `topic-change-detector.sh` | UserPromptSubmit | Detects topic changes during plan execution |
| `get-back-to-work-claude.sh` | Stop | Multi-check grading gate before session stop |
| `task-completion-validator.sh` | PostToolUse | Validates task completion claims against plan |
| `subagent-circuit-breaker.sh` | PreToolUse/PostToolUse | Detects context-limit failures in subagents |

8 additional hooks handle episodic memory sync, poll deduplication, MCP state logging, and other internal concerns. Shared libraries (`hook-logger.sh`, `plan-validator.sh`) provide logging, DB access, and LLM validation utilities.

Hooks deploy to `~/.claude/ironclaude-hooks/` via `make deploy-hooks` and run from there at runtime (not from the repo directory).

### Model Configuration

The Brain selects worker type per task, and each worker gets an advisor **one tier up** for oversight:

| Worker Type | Description | Advisor |
|-------------|-------------|---------|
| `claude-sonnet` | Default. Used for most tasks | Opus |
| `claude-opus` | Full Opus worker for high-complexity tasks | Fable |
| `claude-fable` | Fable model for the hardest, correctness-critical architectural work | — (top tier) |
| `ollama` | Local LLM routed via `ANTHROPIC_BASE_URL` — see [Ollama Workers](#ollama-workers) | — |

**Model tiering — capability on demand.** The always-on Brain runs on **Sonnet** by default and handles routine orchestration itself. When a directive is harder than it can confidently decide, it doesn't guess: it consults an Opus worker for the approach and the right worker tier, then spawns `claude-opus` or `claude-fable` as advised (the spawn-time grader can recommend `claude-fable`, and an approved Opus spawn escalates to Fable only when the grader explicitly recommends it). Combined with the one-tier-up advisors above — a Sonnet worker gets an Opus advisor, an Opus worker gets a Fable advisor — the system delivers **Fable-level capability on the hardest work without burning Fable tokens continuously**: the persistent component stays cheap, and the top tiers are reached transiently, on demand.

The grader defaults to Opus. You can override any of these via environment variables to pin a specific model version:

```bash
# Override all opus-class usage (grader, opus workers) with a single var
export ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-4-6-20250115"

# Or override individually (takes precedence over ANTHROPIC_DEFAULT_OPUS_MODEL)
export BRAIN_MODEL="sonnet"                              # the Brain (default: sonnet)
export GRADER_MODEL="claude-sonnet-4-5-20241022"
```

Advisor pairing is configured under `advisor.advisor_models` (per worker type) with a scalar `advisor.advisor_model` fallback; see the [Configuration Reference](#configuration-reference).

### Worker Skills

| Skill | Purpose |
|-------|---------|
| `activate-professional-mode` | Enable workflow discipline |
| `deactivate-professional-mode` | Disable workflow discipline |
| `brainstorming` | Turn ideas into designs through collaborative dialogue |
| `writing-plans` | Create bite-sized implementation plans |
| `executing-plans` | Execute plans task-by-task with review gates |
| `code-review` | Review work against plan for quality and compliance |
| `systematic-debugging` | Root-cause investigation before proposing fixes |
| `testing-theatre-detection` | Detect tests that can't prevent regressions |
| `elements-of-style` | Apply Strunk & White principles to technical writing |
| `setup-ollama-validation` | Configure local LLM for hook validation |

### Uninstall

```bash
# From within Claude Code:
/plugin uninstall ironclaude
```

This removes the plugin, its hooks, and its MCP server configurations from your Claude Code settings. Your project files and git history are not affected.

---

## Quick Start: Commander (Multi-Session Orchestrator)

The Commander orchestrates multiple Claude Code worker sessions via Slack. A Brain session acts as your autonomous proxy -- decomposing objectives into tasks, spawning workers, reviewing results, and escalating decisions to you. You interact through natural Slack conversation, from any device.

**The free version of Slack works fine.** No paid plan required.

### Prerequisites

- Everything from the Worker prerequisites above
- Python 3.11+
- [tmux](https://github.com/tmux/tmux/wiki/Installing)
- A Slack workspace where you can create apps (free tier works)
- **macOS or Linux** (Commander uses POSIX process management: `os.fork`, `os.setpgid`, `fcntl.flock`)

### Slack App Setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** then **From scratch**
2. **Settings > Socket Mode** -- Enable it, generate an App-Level Token with `connections:write` scope, and save it as `SLACK_APP_TOKEN`
3. **OAuth & Permissions > Bot Token Scopes** -- Add these scopes:
   - `channels:history` -- read channel messages
   - `channels:read` -- list channels
   - `chat:write` -- send messages
   - `commands` -- slash commands
   - `files:write` -- upload log files and diffs
   - `files:read` -- download operator-uploaded images (required for image viewing)
   - `im:history` -- receive direct messages
   - `reactions:read` -- read emoji reactions (for directive approval)
   - `reactions:write` -- add/remove emoji reactions (status indicators)
4. **OAuth & Permissions > User Token Scopes** (optional, for operator message search):
   - `files:write` -- upload files on your behalf
   - `search:read` -- search workspace messages
5. **Event Subscriptions** -- Enable, then subscribe to these bot events:
   - `message.channels` -- messages in channels the bot is in
   - `message.im` -- direct messages to the bot
   - `reaction_added` -- emoji reactions (used for directive confirm/reject)
6. **Slash Commands** -- Create ONE slash command:

   | Command | Description |
   |---------|-------------|
   | `/ironclaude` | All IronClaude controls (status, approve, reject, etc.) |

   Subcommands are passed as text: `/ironclaude status`, `/ironclaude approve <worker-id>`, etc.

7. **Install to Workspace** -- Copy the `SLACK_BOT_TOKEN` from the OAuth page. If you added User Token Scopes, also copy the `SLACK_USER_TOKEN`.
8. **Get your Slack user ID** -- Click your profile picture in Slack, then "Profile". Click the three dots (⋯) and "Copy member ID". Save this as `SLACK_OPERATOR_USER_ID`.
9. **Get your channel ID** -- Right-click the channel in Slack, click "Copy Link", and use the last segment of the URL
10. **Invite the bot** -- In your Slack channel, type `/invite @YourBotName`

### Install and Run

```bash
cd commander
python -m venv .venv && source .venv/bin/activate
cp .env.example .env
```

Edit `commander/.env` with your tokens. This file is gitignored and never committed:

```env
# Required
SLACK_BOT_TOKEN=xoxb-...        # From OAuth & Permissions page
SLACK_APP_TOKEN=xapp-...        # From Socket Mode page (App-Level Token)
SLACK_CHANNEL_ID=C...           # Channel ID from step 9
OPERATOR_NAME=YourName          # Your name (used in Slack messages)

# Optional — enables operator message search
SLACK_USER_TOKEN=xoxp-...       # From OAuth & Permissions page (User OAuth Token)
SLACK_OPERATOR_USER_ID=U...     # Your Slack member ID from step 8
```

Then install and start:

```bash
make install   # Install Python dependencies (includes Anthropic's proprietary claude-agent-sdk)
make run       # Start the daemon
```

The daemon connects to Slack, spawns the Brain session, and begins listening for objectives. Runtime configuration is in `config/ironclaude.json` (relative to the `commander/` directory). Daemon logs are written to `/tmp/ic-logs/` by default (configurable via `log_dir`).

### Daily Usage

1. **Set an objective** -- For example, to task a worker with adding OAuth2 login, you'd type: `/ironclaude objective Add OAuth2 login with Google and GitHub providers`
2. **The Brain decomposes it** -- The Brain analyzes the objective, breaks it into tasks, and spawns worker sessions
3. **Workers execute under discipline** -- Each worker runs Claude Code with professional mode active, following the full brainstorm-plan-execute workflow with review gates
4. **Supervise from Slack** -- Review plans with `/ironclaude detail`, approve with `/ironclaude approve`, check progress with `/ironclaude status`
5. **The Brain manages the rest** -- It reviews completed work, reassigns failed tasks, and escalates decisions it can't make autonomously
6. **Switch accounts when limits hit** -- If the active account hits its usage limit, IronClaude posts a throttled "⚠️ Usage limit hit — send `login` to switch accounts" alert. Send `login` (or `/ironclaude login`); the daemon relays a `claude auth login` sign-in URL to Slack — authorize it in a browser and reply `login code <the-code>` — then it verifies the new account and restarts the Brain + workers onto it. A failed or timed-out sign-in leaves the previous account untouched.

You can do all of this from your phone.

### How Slack Output Is Organized

The Brain can be chatty, so the Commander organizes what reaches you rather than dumping everything into one channel. (The Brain also posts a periodic **heartbeat** — a short status summary of what the workers are doing, roughly every 15 minutes.)

- **Direct replies are threaded under your message.** When you ask the Brain something in Slack, its answer comes back as a threaded reply to your message, and your message gets a ✅ reaction once it's answered — so you can tell a reply landed without scrolling the channel.
- **Routine detail is threaded under the heartbeat.** Tactical chatter (progress notes, retries, "still working on X") is threaded under the most recent heartbeat instead of the main channel — expand the thread if you want it, ignore it if you don't.
- **The main channel stays signal.** Directive status, plans awaiting approval, and escalations post to the channel itself.
- **"Waiting on you" escalations link back to context.** When the Brain needs a decision from you, the alert includes a permalink that jumps straight to the message or context that needs your attention.

Earlier versions kept the channel readable by silently discarding any Brain message that didn't reference a tracked directive — which also threw away the Brain's direct answers to your questions. Threading replaces that: nothing meaningful is dropped, but noise is tucked into threads instead of the main feed. (The one exception still suppressed is the Brain echoing the daemon's own internal control markers back at it, which would otherwise loop.)

### Worker Reliability

The daemon monitors all active workers for stalled output. When a worker's log hash stops changing, a stale timer starts. Before killing, the daemon checks for CPU activity — if the process is still running hot, the kill is deferred 15 minutes. The daemon also checks available system memory before spawning workers, blocking new spawns when memory drops below the `min_available_memory_pct` threshold.

Thresholds are stage-aware:

| Situation | Alert | Kill |
|-----------|-------|------|
| Normal worker | 30 min | 60 min |
| Brainstorming/debugging | ~22 min | ~45 min (0.75× multiplier) |
| Executing/reviewing | ~45 min | ~90 min (1.5× multiplier) |
| Prompt-waiting (blocked on AskUserQuestion) | 15 min | 30 min |

Stuck escalation is two-step: at the alert threshold, the Brain receives a `[STUCK]` alert with the worker ID and idle duration. At the kill threshold, the operator receives a Slack notification (`*Worker Stuck-Killed:*`) with duration and stage, then the Brain receives a `[MANDATORY SWEEP]` prompt listing remaining directives to re-evaluate.

### Ollama Workers

Workers can run against local Ollama models. The daemon sets `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN=ollama` before spawning, routing Claude Code's API calls to the Ollama endpoint. This is separate from the hook validation backend — hook validation uses a small model for fast gate checks; Ollama workers use a full coding model as the worker's primary LLM.

Workers can also run on remote machines via SSH — configure machine definitions in `config/machines.yaml`. For local Ollama machines, `machines` is a top-level key in `ironclaude.json`:

```json
"machines": [
  {
    "name": "laptop",
    "url": "http://localhost:11434",
    "model": "qwen3-coder-next:q2_k",
    "max_workers": 1
  }
]
```

Before spawning an Ollama worker, the daemon checks how much VRAM Ollama already has loaded and **blocks the spawn if that exceeds `ollama_vram_block_threshold_gb`** (default: 8.0 GB) — it is a ceiling on already-loaded VRAM, not a minimum. The 8 GB default suits Apple Silicon unified memory, where Ollama competes with everything else for the shared pool. Raise it on machines with more memory headroom (see [Running Ollama workers on Apple Silicon](#running-ollama-workers-on-apple-silicon)). Models can be unloaded on demand via the Brain's MCP tools.

#### Running Ollama workers on Apple Silicon

Ollama workers run against a local Ollama, with the model launched *under the hood of a full Claude Code session*. Claude Code's first turn needs ~26k tokens, far above Ollama's 4096 default, so the daemon builds a `num_ctx`-fixed model variant (`ic-<model>-<num_ctx>`) and points the worker at it.

The two knobs interact: a larger `ollama_worker_num_ctx` means a larger resident model, which must still pass the `ollama_vram_block_threshold_gb` ceiling. The defaults (32k context, 8 GB ceiling) are tuned to work together out of the box.

To run a larger context window, raise both. On a 48 GB M4 Max the following works well — a 128k variant sits ~9.12 GB fully on-GPU, so the ceiling must be raised above it:

```json
{
  "ollama_vram_block_threshold_gb": 24.0,
  "ollama_worker_num_ctx": 131072
}
```

### Autonomy Levels

Set `AUTONOMY_LEVEL` in `.env` to control how independently the Brain operates:

| Level | Behavior |
|-------|----------|
| 1 | Confirms every action with you |
| 3 | Confirms strategic decisions, executes tactical ones autonomously (default) |
| 5 | Full autonomy -- escalates only when blocked |

### Uninstall

```bash
# Stop the daemon
make stop   # or Ctrl-C if running in foreground

# Remove the commander
rm -rf commander/

# Remove the worker plugin (from within Claude Code):
/plugin uninstall ironclaude
```

---

## Architecture

```
+---------------------------------------------+
|                  IronClaude                  |
+----------------------+----------------------+
|       Worker         |      Commander       |
|  (Claude Code Plugin)|  (Python Daemon)     |
|                      |                      |
|  - Professional Mode |  - Brain Session     |
|  - Skills & Hooks    |  - Worker Sessions   |
|  - MCP State Manager |  - Slack Interface   |
|  - Episodic Memory   |  - tmux Management   |
|  - Code Review Gates |  - Objective Tracking|
+----------------------+----------------------+
```

The Worker enforces discipline on individual Claude Code sessions. The Commander orchestrates multiple Worker sessions, each running under professional mode, while you supervise from Slack.

---

## The Grader

Every significant Brain decision passes through an independent quality gate: the Grader. This is a persistent Claude Opus session that evaluates whether the Brain's proposed actions meet your standards — before they execute.

### What It Evaluates

The Grader runs at four critical decision points:

1. **Spawning workers** -- Is the objective specific enough? Does it include exact file paths, success criteria, and constraints? Is the worker type (Sonnet vs Opus) appropriate for the complexity?
2. **Sending messages to workers** -- Does the message respect the workflow? Does it try to skip stages or characterize work as trivial?
3. **Killing workers** -- Is there concrete evidence the objective was completed? Git diffs, test results, not just the worker's claim?
4. **Batch spawning** -- All objectives in a batch are graded together for consistency

### How It Works

The Grader runs as its own Claude Opus session in a dedicated tmux pane. When the Brain wants to take an action, the MCP server intercepts the call, sends the proposed action to the Grader with a specialized evaluation prompt, and waits for a structured response: a letter grade (A-F), an approved/rejected decision, and specific feedback.

If the Grader rejects, the Brain gets the feedback and must revise before retrying. If the Grader approves, the action proceeds. The Brain cannot bypass the Grader — it's enforced at the MCP server level, not by prompt instructions.

### Why It Matters

Without the Grader, the Brain could:
- Spawn workers with vague objectives that waste compute ("make it better")
- Tell workers to skip workflow stages ("just get it done, skip brainstorming")
- Accept worker claims of completion without verifying actual results
- Use overpowered models for simple tasks, or underpowered models for complex ones

The Grader catches these patterns because it evaluates each decision against a strict checklist — the same standards you'd apply if you were reviewing every Brain action yourself. It's the enforcement layer that makes autonomous operation trustworthy.

---

## Wiki Knowledge Layer

IronClaude implements [Andrej Karpathy's LLM wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) as a synthesis layer between episodic memory (raw conversation archives) and rules (CLAUDE.md + behavioral directives). The Brain maintains a persistent wiki of markdown pages at `~/.ironclaude/brain/wiki/`, automatically synthesizing patterns and decisions from episodic memory into structured, queryable knowledge.

Three convergence mechanisms keep the wiki current:
- **Post-directive ingest** — captures learnings after each completed directive
- **Periodic sweeps** — fills gaps by searching episodic memory for uncaptured patterns
- **Search-triggered synthesis** — evaluates every episodic memory search for wiki-worthy knowledge

---

## Configuration Reference

`config/ironclaude.json` controls Commander runtime behavior. All keys are optional; unset keys use defaults.

| Key | Default | Description |
|-----|---------|-------------|
| `poll_interval_seconds` | `15` | Worker status poll interval (seconds) |
| `heartbeat_interval_seconds` | `900` | Brain heartbeat check interval (15 min) |
| `worker_stale_threshold_seconds` | `300` | Seconds before a worker is considered stale |
| `brain_timeout_seconds` | `600` | Max seconds to wait for a Brain response |
| `max_worker_retries` | `3` | Retry attempts before marking a task failed |
| `autonomy_level` | `"3"` | Brain autonomy: `1` = confirm all, `3` = default, `5` = full |
| `brain_model` | `"sonnet"` | Model for the Brain session (escalates to opus/fable workers on demand) |
| `grader_model` | `"opus"` | Model for the Grader session |
| `effort_level` | `"high"` | Worker effort level (`CLAUDE_CODE_EFFORT_LEVEL`) |
| `machines` | `[]` | Named Ollama machine configs — see [Ollama Workers](#ollama-workers) |
| `advisor.enabled` | `true` | Whether workers get a one-tier-up advisor session |
| `advisor.executor_model` | `"sonnet"` | Default executor worker model |
| `advisor.advisor_models` | `{"claude-sonnet": "opus", "claude-opus": "fable"}` | One-tier-up advisor model per worker type |
| `advisor.advisor_model` | `"opus"` | Fallback advisor model for worker types not in `advisor_models` |
| `dispatch.use_goal` | `false` | When true, spawned workers are given a `/goal` completion condition for more autonomous execution |
| `ollama_vram_block_threshold_gb` | `8.0` | Max already-loaded Ollama VRAM (GB) tolerated before a spawn is **blocked** (a ceiling, not a minimum) |
| `ollama_worker_num_ctx` | `32768` | Context window (`num_ctx`) baked into the Ollama worker model variant. 32k (~7.5 GB) fits under the 8 GB default ceiling; raise for longer context if your hardware allows |
| `ollama_worker_max_output_tokens` | _(unset)_ | When set, exports `CLAUDE_CODE_MAX_OUTPUT_TOKENS` for Ollama workers to cap runaway output |
| `push_enabled` | `false` | Enable automated git push after task completion |
| `push_max_per_hour` | `5` | Max auto-pushes per hour when `push_enabled` is true |
| `brain_heartbeat_timeout_seconds` | `60` | Max seconds before Brain is considered dead |
| `min_available_memory_pct` | `0.10` | Memory pressure threshold (fraction) for worker spawn |
| `tmp_dir` | `"/tmp/ic"` | Temp directory for daemon artifacts |
| `log_dir` | `"/tmp/ic-logs"` | Log directory |
| `db_path` | `"data/db/ironclaude.db"` | Commander database path |
| `brain_cwd` | `"~/.ironclaude/brain"` | Brain session working directory |
| `brain_prompt_path` | `""` | Path to brain prompt file |
| `operator_name` | `"Operator"` | Operator display name |

**Environment variable overrides** (take precedence over `ironclaude.json`):
`POLL_INTERVAL_SECONDS`, `HEARTBEAT_INTERVAL_SECONDS`, `DB_PATH`, `BRAIN_TIMEOUT_SECONDS`, `BRAIN_MODEL`, `GRADER_MODEL`, `EFFORT_LEVEL`

---

## Security Model

Commander workers run with `--dangerously-skip-permissions`. This is intentional.

Professional mode hooks are the security model. By the time a Commander worker is executing, the work has already passed through brainstorming, a written implementation plan, your approval, and code review gates. Asking for per-action permission at execution time is redundant friction on work that was already authorized — the discipline is enforced upstream, not at the action level.

Users who prefer Claude Code's built-in permission prompts can use [Anthropic's auto mode](https://www.anthropic.com/engineering/claude-code-auto-mode) as an alternative to Commander's worker spawning.

**Use at your own risk.** IronClaude is open-source software provided under the MIT License with no warranty. You are responsible for everything IronClaude does on your systems. Review the [LICENSE](LICENSE) before use.

---

## Dependencies & Licensing

IronClaude is licensed under the [MIT License](LICENSE).

### External Dependencies

**Worker (Node.js):**
- `@modelcontextprotocol/sdk` -- MIT
- `better-sqlite3` -- MIT
- `zod` -- MIT

**Commander (Python):**
- `slack-sdk`, `slack-bolt` -- MIT
- `mcp` -- MIT
- `duckduckgo_search` -- MIT
- `markdownify` -- MIT

### Anthropic SDK Notice

The Commander depends on `@anthropic-ai/claude-agent-sdk`, which is **proprietary software owned by Anthropic PBC**. This SDK is **not redistributed** with IronClaude -- it is listed as a dependency and installed separately by users via `pip install`. By installing the Claude Agent SDK, you agree to [Anthropic's terms of service](https://code.claude.com/docs/en/legal-and-compliance).

IronClaude's MIT license applies only to IronClaude's own code, not to Anthropic's SDK.

### Episodic Memory Attribution

The episodic-memory MCP server is derived from [obra/episodic-memory](https://github.com/obra/episodic-memory) by Jesse Vincent (MIT License). See `worker/mcp-servers/episodic-memory/LICENSE` for attribution.

---

## macOS Prerequisites

macOS has two install quirks worth knowing about when setting up IronClaude via Homebrew: Apple ships an old Bash, and `brew install node` resolves to a node version newer than the one IronClaude's bundled `better-sqlite3` currently builds against.

**Recommended install (Apple Silicon):**

```bash
# 1. Modern Bash, discoverable through default macOS PATH.
HOMEBREW_NO_INSTALL_CLEANUP=1 brew install bash
sudo ln -s /opt/homebrew/bin/bash /usr/local/bin/bash

# 2. Node 24 LTS (pin explicitly; generic `node` resolves to a newer major
#    that the bundled native modules don't yet support).
HOMEBREW_NO_INSTALL_CLEANUP=1 brew install node@24
brew link --overwrite node@24
```

**Why the Bash symlink:** Apple ships Bash 3.2 at `/bin/bash` (the last GPLv2 release), and IronClaude hooks like `professional-mode-guard.sh` use Bash 4+ features. `hooks.json` invokes hooks as `bash <script>` — so the system needs to resolve the bare command `bash` to a modern build (editing hook shebangs does NOT help). The default macOS PATH (`/etc/paths` = `/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`) puts `/usr/local/bin` ahead of `/bin`, so the symlink makes Bash 4+ discoverable regardless of how Claude Code is launched (Spotlight, dock, terminal). On Intel Macs, Homebrew links Bash into `/usr/local/bin/bash` directly — the explicit symlink step is unnecessary.

**Why `HOMEBREW_NO_INSTALL_CLEANUP=1`:** without it, `brew install` can autoremove formulae that Homebrew considers orphaned dependencies of untrusted taps. `node` is a common casualty if a previously-trusted tap got revoked. The flag suppresses cleanup for that single command.

**Symptoms if you skip these steps on macOS:**
- Bash 3.2 still active: every SessionStart prints `bash 3.2.57(1)-release detected — hooks require bash 4+` and some hook functionality is unreliable.
- `brew install node` chosen instead of `node@24`: `claude mcp list` shows `plugin:ironclaude:state-manager: ✘ Failed to connect`, and `~/.claude/ironclaude-mcp-state-manager.log` contains a `NODE_MODULE_VERSION` mismatch from `better-sqlite3`.
- node uninstalled entirely (no `node` on PATH): IronClaude's MCP servers cannot launch on macOS.

---

## Windows Prerequisites

On Windows, launch Claude Code from **Git Bash** (not PowerShell). The plugin's bash hooks require the MSYS2/Git Bash environment.

Required tools (install via [Chocolatey](https://chocolatey.org/) in Administrator PowerShell):

```powershell
choco install jq sqlite -y
```

Or run the automated installer:
```powershell
.\scripts\install-windows-prerequisites.ps1
```

After installing the plugin, fix CRLF line endings on hook scripts:
```bash
sed -i 's/\r$//' ~/.claude/plugins/cache/ironclaude/ironclaude/*/hooks/*.sh
```

For the full Windows setup guide including path handling, bash environment configuration, and troubleshooting, see [WINDOWS_SETUP.md](WINDOWS_SETUP.md).

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Use `/ironclaude:activate-professional-mode` for all changes
4. Follow the brainstorm, plan, execute workflow
5. Submit a PR

## About

IronClaude was developed by [Robert Hyatt](https://www.linkedin.com/in/robert-hyatt/) while working on his solo indie dev side project, Artificial Adventures — a game approaching alpha testing. Inspired by [obra/superpowers](https://github.com/obra/superpowers).

If you're interested in IronClaude or want to talk about Artificial Adventures, [reach out on LinkedIn](https://www.linkedin.com/in/robert-hyatt/).
