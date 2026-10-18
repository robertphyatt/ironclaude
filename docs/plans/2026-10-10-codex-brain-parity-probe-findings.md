# Codex Brain Parity: Hook Evidence Findings

Date: 2026-10-11

## Step 1: state-cache writers

```bash
grep -n -e "state-cache" /Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh /Users/roberthyatt/Code/ironclaude/worker/hooks/session-init.sh
```
Output:
```
/Users/roberthyatt/Code/ironclaude/worker/hooks/session-init.sh:284:find "$HOME/.claude" -name "ironclaude-state-cache-*.json" -mtime +0 -delete 2>/dev/null || true
/Users/roberthyatt/Code/ironclaude/worker/hooks/state-activator.sh:217:STATE_CACHE="$HOME/.claude/ironclaude-state-cache-${SESSION_TAG}.json"
```

## Step 2: Brain state-cache timestamp

```bash
grep -o -e "\"timestamp\": *\"[^\"]*\"" /Users/roberthyatt/.claude/ironclaude-state-cache-01a127b6-a9dc-78f1-9daf-b204d6bd771c.json
```
Output:
```
"timestamp": "2026-10-10T22:09:07Z"
```

## Step 3: Brain turn start in the same second

```bash
grep -o -e "2026-10-10T22:09:0[0-9.]*Z" /Users/roberthyatt/.codex/sessions/2026/10/10/rollout-2026-10-10T15-27-24-01a127b6-a9dc-78f1-9daf-b204d6bd771c.jsonl
```
Output (first 5 lines shown; first line is the one the plan cites):
```
2026-10-10T22:09:07.629Z
2026-10-10T22:09:07.639Z
2026-10-10T22:09:07.640Z
2026-10-10T22:09:07.640Z
2026-10-10T22:09:07.707Z
```

## Step 4: PreToolUse blocks in the Brain's app-server

```bash
grep -c "Command blocked by PreToolUse hook" /Users/roberthyatt/.codex/sessions/2026/10/10/rollout-2026-10-10T15-27-24-01a127b6-a9dc-78f1-9daf-b204d6bd771c.jsonl
```
Output:
```
1
```

## Conclusions

- UserPromptSubmit fires inside the Codex Brain's app-server for a trusted entry.
- PreToolUse fires there too.
- Not proven: turn 1, and new entries before they are trusted. Task 12's fail-closed canary covers both.
- No payload probe was run. The hooks match every known shape, the `spawn_agent` payload remains uncaptured, and Task 14 is the live proof.
