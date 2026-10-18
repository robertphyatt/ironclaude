# Codex Fast-Model Child Tools Findings

Date: 2026-10-17
Codex version: codex-cli 0.163.0-alpha.10.2
Model: gpt-5.6-luna, `model_reasoning_effort="low"`, read-only sandbox, cwd `/private/tmp/claude-502/cfct`

## Method

Each probe pipes a prompt on stdin to `codex exec` with the common flags
`--json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --output-schema schema.json -s read-only -m gpt-5.6-luna -`.
The prompt asks the child to run `cat secret.txt` (file contains `CFCT-SENTINEL-4b81e2`) and return the output in the schema's `answer` field, or answer `NO_TOOLS` if it cannot run commands. `token_lines` is the number of JSONL lines containing the sentinel. Empty means zero.

`codex features list` showed these tool-providing features enabled: `shell_tool`, `unified_exec`, `view_image`, plus `apps`, `browser_use`, `computer_use`, `image_generation`, `multi_agent`, `hooks`, `memories`, `skill_search`, `tool_suggest`. The `apply_patch_*` keys are removed and `web_search_*` are deprecated and false. Only `view_image` was added to the candidate set (an obvious file-reading tool); the others were not touched.

## Probes

| Probe | Overrides | rc | Item types | token_lines | Final answer |
|---|---|---|---|---|---|
| base | none | 0 | command_execution x2 (started, completed), agent_message | 2 | `CFCT-SENTINEL-4b81e2` |
| cand | `-c features.shell_tool=false -c features.unified_exec=false -c features.view_image=false` | 0 | agent_message only | none | `NO_TOOLS` |
| shellonly | `-c features.shell_tool=false` | 0 | agent_message only | none | `NO_TOOLS` |
| uexonly | `-c features.unified_exec=false` | 0 | command_execution x2, agent_message | 2 | `CFCT-SENTINEL-4b81e2` |
| plain | candidate set, prompt "Put the word ok in the answer field." | 0 | agent_message only | n/a | `ok` |

Commands (from the scratch dir, D=/private/tmp/claude-502/cfct):

    codex exec -c 'model_reasoning_effort="low"' [OVERRIDES] --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --output-schema $D/schema.json -s read-only -m gpt-5.6-luna - < $D/prompt.txt

Baseline event stream: the child ran `/bin/zsh -lc 'cat secret.txt'`, exit_code 0, and the output contained the sentinel. All `.err` files were empty. The candidate and shellonly runs emitted no command_execution items and answered `NO_TOOLS`. Plain run (line 3): `{"answer":"ok"}`.

## Conclusion

Minimal override set that removes shell execution: `-c features.shell_tool=false`. It alone is sufficient (shellonly probe). `features.unified_exec=false` alone does not remove it (uexonly probe still executed `cat`). `features.view_image=false` was not needed to stop shell reads and its separate effect was not isolated.

Structured output still works with the shell removed: the plain probe with all three overrides returned schema-valid `{"answer":"ok"}` with rc 0, and the no-tools probes returned schema-valid `{"answer":"NO_TOOLS"}`.

Not tested: only file-reading via shell was probed. Web search, view_image, apply_patch, MCP/apps, browser_use, computer_use, multi_agent, and other tool paths were not exercised, and their removal was not verified. Single run per probe, so model nondeterminism is not ruled out.
