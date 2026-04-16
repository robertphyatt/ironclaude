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
```

> **First run:** The first time MCP servers start (~10-30s), they auto-build platform-specific binaries. Subsequent starts are instant. If startup fails, check `~/.claude/ironclaude-mcp-state-manager.log` and `~/.claude/ironclaude-mcp-episodic-memory.log`.

### Activate Professional Mode

```
/ironclaude:activate-professional-mode
```

When active, Claude operates in architect mode -- planning and designing without making code changes unless executing an approved plan. Every action is validated by hooks that check whether it's permitted in the current workflow phase.

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

The daemon connects to Slack, spawns the Brain session, and begins listening for objectives. Runtime configuration is in `config/ironclaude.json`.

### Daily Usage

1. **Set an objective** -- For example, to task a worker with adding OAuth2 login, you'd type: `/ironclaude objective Add OAuth2 login with Google and GitHub providers`
2. **The Brain decomposes it** -- The Brain analyzes the objective, breaks it into tasks, and spawns worker sessions
3. **Workers execute under discipline** -- Each worker runs Claude Code with professional mode active, following the full brainstorm-plan-execute workflow with review gates
4. **Supervise from Slack** -- Review plans with `/ironclaude detail`, approve with `/ironclaude approve`, check progress with `/ironclaude status`
5. **The Brain manages the rest** -- It reviews completed work, reassigns failed tasks, and escalates decisions it can't make autonomously

You can do all of this from your phone.

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
