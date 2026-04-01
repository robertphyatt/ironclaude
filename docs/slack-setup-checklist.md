# IronClaude Slack App Setup Checklist

Use this checklist to configure all required Slack app settings at [api.slack.com/apps](https://api.slack.com/apps) in one pass.

> **Reusing an existing Tron bot?** You can reuse the bot tokens — just update the app's display name and bot username in **Settings > Basic Information** and **App Home**.

---

## 1. App Creation

- [ ] Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
- [ ] Name the app `IronClaude` (or your preferred name)
- [ ] Select your workspace

---

## 2. Socket Mode

- [ ] Go to **Settings > Socket Mode** → Enable Socket Mode
- [ ] Generate an **App-Level Token** with the `connections:write` scope
- [ ] Save the token as `SLACK_APP_TOKEN` in your `.env`

---

## 3. Bot Token Scopes

Go to **OAuth & Permissions > Scopes > Bot Token Scopes** and add:

| Scope | Purpose |
|-------|---------|
| `chat:write` | Post messages to the channel |
| `channels:history` | Read message history (for polling incoming messages) |
| `channels:read` | Read channel metadata |
| `commands` | Receive slash commands |
| `reactions:write` | Add/remove emoji reactions to messages (directive status indicators) |
| `reactions:read` | Read reactions on messages (for emoji reconciliation) |
| `files:write` | Upload log files to Slack |

- [ ] After adding scopes, click **Install to Workspace** (or **Reinstall**)
- [ ] Copy the `Bot User OAuth Token` (`xoxb-...`) and save as `SLACK_BOT_TOKEN` in your `.env`

---

## 4. Event Subscriptions

- [ ] Go to **Event Subscriptions** → Enable Events
- [ ] Under **Subscribe to bot events**, add: `message.channels`, `reaction_added`

---

## 5. Slash Commands

Go to **Slash Commands** and create each of the following. For each command:
- **Request URL**: Leave blank (Socket Mode handles routing — no URL needed)
- **Escape channels, users, and links**: leave unchecked

| Command | Description |
|---------|-------------|
| `/icstatus` | Show current state: brain, workers, objective, progress |
| `/icobjective` | Set a new objective: `/icobjective <text>` |
| `/icapprove` | Approve a worker's plan: `/icapprove <worker-id>` |
| `/icreject` | Reject a worker's plan: `/icreject <worker-id>` |
| `/icdetail` | Show worker details: `/icdetail <worker-id>` |
| `/iclog` | Show worker log: `/iclog <worker-id> [lines]` |
| `/icstop` | Kill all workers and shut down |
| `/icpause` | Pause new work, let current finish |
| `/icresume` | Resume from pause |
| `/ichelp` | Show available commands |
| `/icsummary` | Directive status report: in-progress, blocked, completed |

- [ ] All 11 slash commands created

---

## 6. Channel Setup

- [ ] In your Slack workspace, invite the bot: `/invite @IronClaude`
- [ ] Get your channel ID: right-click the channel → **Copy Link** → use the last segment of the URL (starts with `C`)
- [ ] Save the channel ID as `SLACK_CHANNEL_ID` in your `.env`

---

## 7. Final `.env` Checklist

Confirm these are set in `commander/.env`:

```
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
OPERATOR_NAME=YourName
```
