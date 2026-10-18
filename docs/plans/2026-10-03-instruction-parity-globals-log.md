# Instruction parity: Task 4 globals log (2026-10-03)

## Backups
- /Users/roberthyatt/.claude/CLAUDE.md.bak-2026-10-03
- /Users/roberthyatt/.codex/AGENTS.md.bak-2026-10-03
- /Users/roberthyatt/.codex/rules/default.rules.bak-2026-10-03

## Step 2: starting state (before edits)
```
diff ~/.claude/CLAUDE.md ~/.codex/AGENTS.md   (exit 1)
3c3
< # Behavioral Directives for Claude
---
> # Behavioral Directives for Codex

wc -l ~/.codex/rules/default.rules
      17 /Users/roberthyatt/.codex/rules/default.rules

grep -n -F 'pattern=["codex", "exec"]' ~/.codex/rules/default.rules
3:prefix_rule(pattern=["codex", "exec"], decision="allow")
```

## Steps 5 and 7: verification (after edits)
```
--- claude diff
39c39
<    - Use inline execution mode when tasks are complex enough to risk context exhaustion spirals
---
>    - Delegate execution to a focused subagent by default (Sonnet on Claude, Terra on Codex); reserve inline for orchestration itself, a step that genuinely can't be captured in a focused prompt, or a task a prior subagent attempt already spiraled on — the per-task code review, testing-theatre, and tier-up gates catch subagent drift
exit=1
--- codex diff
35c35
<    - Use the ironclaude:search-conversations agent, not raw MCP tools
---
>    - Search with the `episodic-memory` MCP server's search capability
39c39
<    - Use inline execution mode when tasks are complex enough to risk context exhaustion spirals
---
>    - Delegate execution to a focused subagent by default (Sonnet on Claude, Terra on Codex); reserve inline for orchestration itself, a step that genuinely can't be captured in a focused prompt, or a task a prior subagent attempt already spiraled on — the per-task code review, testing-theatre, and tier-up gates catch subagent drift
exit=1
--- cross diff
3c3
< # Behavioral Directives for Claude
---
> # Behavioral Directives for Codex
35c35
<    - Use the ironclaude:search-conversations agent, not raw MCP tools
---
>    - Search with the `episodic-memory` MCP server's search capability
exit=1
--- rules diff
3d2
< prefix_rule(pattern=["codex", "exec"], decision="allow")
7,17d5
< prefix_rule(pattern=["rg", "^ *22703 |^ *34012 |^ *34014 |^ *34079 |^ *34080 |^ *34704 |^ *34708 |^ *34751 |^ *34761 "], decision="allow")
< prefix_rule(pattern=["rg", "/Users/roberthyatt/\\.codex/plugins/cache/ironclaude/ironclaude/1\\.1\\.2/.*/(state-manager|episodic-memory)"], decision="allow")
< prefix_rule(pattern=["/usr/bin/python3", "-c", "import subprocess; subprocess.Popen([\"/usr/bin/python3\", \"/private/tmp/restart_codex.py\"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"], decision="allow")
< prefix_rule(pattern=["git", "add", "--", "worker/hooks/state-activator.sh", "worker/mcp-servers/state-manager/dist/index.js", "worker/mcp-servers/state-manager/src/__tests__/session-identity.test.ts", "worker/mcp-servers/state-manager/src/session-identity.ts", "worker/skills/deactivate-professional-mode/SKILL.md", "commander/tests/test_deactivation_client_parity.py", "worker/hooks/tests/test-professional-mode-deactivation.sh"], decision="allow")
< prefix_rule(pattern=["git", "commit", "-m", "Fix Codex professional-mode activation and deactivation"], decision="allow")
< prefix_rule(pattern=["/bin/zsh", "-lc", "codex exec --json --ephemeral --skip-git-repo-check -s read-only -m gpt-5.6-sol 'Report-only blind adversarial design review. No file reads or edits. Classify verified findings as Critical, Important, Quality, or Informational.\n\nEvidence: Inkarnate catalogue selection scrolls an exact target token, waits 220 ms, separately refreshes geometry and viewport, then a browser evaluation uses elementFromPoint at center. It returns requested image URL only when target is topmost; all other states become null, category visibility, detail covered. It retries once and raises before canvas click. Four comparable live runs placed 9 of 13, 9 of 13, 13 of 13, and 9 of 13 groups. A 700 ms fix claim is invalid because patch never applied. Candidate causes are tooltip, sticky Recently Used header, or unsettled grid.\n\nDesign: replace scalar observation with one atomic structured browser hit test. Return target match plus bounded sanitized elementsFromPoint stack with tag, id, classes, role, aria label, short text, rect, target rect, and target rank. Add immutable machine-readable obstruction diagnostic to SelectionError and report while preserving category, detail, one retry, and fail-closed behavior. Blueprint runner prints compact diagnostic. No full DOM, screenshot, longer timeout, weaker guard, extra retries, or deletion. Tests cover overlay fingerprint, field bounds and sanitization, propagation, and no canvas click. Live probe uses a uniquely titled blueprint map. Captured evidence selects only a predetermined generic repair branch; then identical repeated live runs plus focused and full tests.\n\nAnswer: Is measurement atomic and minimal? Missing fields? Can capture and conditional repair be one plan or must be separate loops? Identify testing-theatre and external-state risks.' < /dev/null"], decision="allow")
< prefix_rule(pattern=["/bin/zsh", "-lc", "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider tools/inkarnate_mcp/tests -q"], decision="allow")
< prefix_rule(pattern=["/bin/zsh", "-lc", "{ sed -n '1,$p' /private/tmp/inkarnate-obstruction-plan-review-preamble.txt; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic-design.md; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic-requirements.md; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic.md; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic.plan.json; } | codex exec --json --ephemeral --skip-git-repo-check -s read-only -m gpt-5.6-sol -"], decision="allow")
< prefix_rule(pattern=["/bin/cp", "/private/tmp/codex-config-full-bypass.toml", "/Users/roberthyatt/.codex/config.toml"], decision="allow")
< prefix_rule(pattern=["/bin/zsh", "-lc", "{ sed -n '1,$p' /private/tmp/inkarnate-obstruction-plan-review-preamble.txt; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic-design.md; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic-requirements.md; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic.md; sed -n '1,$p' docs/plans/2026-08-02-inkarnate-catalogue-obstruction-diagnostic.plan.json; sed -n '1,$p' /private/tmp/inkarnate-obstruction-fix-advisor-addon.txt; } | codex exec --json --ephemeral --skip-git-repo-check -s read-only -m gpt-5.6-sol -"], decision="allow")
< prefix_rule(pattern=["test", "-f", "/private/tmp/restart_codex.py"], decision="allow")
exit=1
--- wc
       5 .codex/rules/default.rules
```

## Note
A removed rule does not block a command; Codex asks for approval instead.
