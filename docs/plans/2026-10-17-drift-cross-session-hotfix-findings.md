# Drift Cross-Session Hotfix Findings

## Task 1 (before change)

### Step 2: trusted hashes (t1-trusted.log, verbatim)
```
162:[hooks.state."ironclaude@ironclaude:hooks/hooks.json:pre_tool_use:3:0"]
163-trusted_hash = "sha256:49ec328041cc162c77ffe9919da64b66c2d6b14670d9d0a1f45f12ddb8eb21b1"
--
228:[hooks.state."ironclaude@ironclaude:hooks/hooks.json:pre_tool_use:6:0"]
229-trusted_hash = "sha256:747d3b903ee389aaf659233bf36103d51371c048ab6584a256a2e2ab3b9830c5"
--
231:[hooks.state."ironclaude@ironclaude:hooks/hooks.json:post_tool_use:6:0"]
232-trusted_hash = "sha256:27182980c0d2e6d7f73e08f9db2486f2ced32622646f2b78a4be5c454b3006dd"
--
255:[hooks.state."ironclaude@ironclaude:hooks/hooks.json:subagent_stop:1:0"]
256-trusted_hash = "sha256:9a32b1a160ce972fc5eab2fdd5c8dc05d2070c00cb55c16b9d1b3cfb2c35e6dd"
```

### Step 2: group indexes (rg output over t1-groups.log, verbatim)
```
4:PreToolUse 3 bash $HOME/.claude/ironclaude-hooks/professional-mode-guard.sh
7:PreToolUse 6 bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh
19:PostToolUse 6 bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh
23:SubagentStop 1 bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh
```

### Step 3: trust probe (t1-probe.log, verbatim)
```
cache_version=1.2.0+codex.20261017222253
hooks_json_repo_vs_cache=identical
== pre_tool_use:3:0 command=bash $HOME/.claude/ironclaude-hooks/professional-mode-guard.sh
trusted=49ec328041cc162c77ffe9919da64b66c2d6b14670d9d0a1f45f12ddb8eb21b1
script_repo=2793bf3de8d4031d50ee72f860fb315e8a969598ea234294bd48e79b467ecacf
script_stable=2793bf3de8d4031d50ee72f860fb315e8a969598ea234294bd48e79b467ecacf
script_cache=2793bf3de8d4031d50ee72f860fb315e8a969598ea234294bd48e79b467ecacf
cmd_raw=5517366d77c7c2e997c5baf4bc73ec4a53f86e0cde50d4ff914703c14aec2621
cmd_lf=af7fbc0c8754c04cc41842886a352479358336b9cd4a5573742f177b35a3fac5
cmd_home=63fa4b5cc9ee59a79d426baad75148c9bb7e8fecf2a6b0468d95ef656308ddf7
handler_json=41a75fdfc4ac06f2a7c6d3601284e5d5093a70f4d92ab45b584f29224b1ffaaf
group_json=18664acd12d7d5899392532cbeb5bef59c63ae5094b9e49db5d70d9fe9ba4c86
== pre_tool_use:6:0 command=bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh
trusted=747d3b903ee389aaf659233bf36103d51371c048ab6584a256a2e2ab3b9830c5
script_repo=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
script_stable=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
script_cache=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
cmd_raw=313550f60ace268d8d69deab99dde58b6b7715edfccc430651166a6e61cfd232
cmd_lf=5f31f9185a5d8017277b1b880a883fb834b104c9a23f92b77c08964b533cec6f
cmd_home=57f95d4d468d16ed66e9ab1b7ebb8c20ee0f5402a542880cd3b833474d9a83e2
handler_json=4fe7a319f2b0e9cfb725e74baeb6a7f2bbff8408bf547f0d742051aa938fc494
group_json=650971cd190bd31a7493c8dfe1ddae4447cb8dd9b17154ed5d8adbbed86d4c87
== post_tool_use:6:0 command=bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh
trusted=27182980c0d2e6d7f73e08f9db2486f2ced32622646f2b78a4be5c454b3006dd
script_repo=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
script_stable=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
script_cache=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
cmd_raw=313550f60ace268d8d69deab99dde58b6b7715edfccc430651166a6e61cfd232
cmd_lf=5f31f9185a5d8017277b1b880a883fb834b104c9a23f92b77c08964b533cec6f
cmd_home=57f95d4d468d16ed66e9ab1b7ebb8c20ee0f5402a542880cd3b833474d9a83e2
handler_json=4fe7a319f2b0e9cfb725e74baeb6a7f2bbff8408bf547f0d742051aa938fc494
group_json=8d131877c433ae6781cfa63e93904ee96818094403f758221f4af3ccb1eceac6
== subagent_stop:1:0 command=bash $HOME/.claude/ironclaude-hooks/execution-drift-check.sh
trusted=9a32b1a160ce972fc5eab2fdd5c8dc05d2070c00cb55c16b9d1b3cfb2c35e6dd
script_repo=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
script_stable=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
script_cache=38f8d767149a349378198c0b5ca6eb5385cd547728851993b8e409e48e0a8d08
cmd_raw=313550f60ace268d8d69deab99dde58b6b7715edfccc430651166a6e61cfd232
cmd_lf=5f31f9185a5d8017277b1b880a883fb834b104c9a23f92b77c08964b533cec6f
cmd_home=57f95d4d468d16ed66e9ab1b7ebb8c20ee0f5402a542880cd3b833474d9a83e2
handler_json=4fe7a319f2b0e9cfb725e74baeb6a7f2bbff8408bf547f0d742051aa938fc494
group_json=e9a2e3eab8e450dd570b7f0b53ab2de670e2a4b897a3c95bb97daa03daf62877
```

### Per-section MATCH result

- pre_tool_use:3:0: none
- pre_tool_use:6:0: none
- post_tool_use:6:0: none
- subagent_stop:1:0: none

Interpretation: no candidate (script_repo/stable/cache, cmd_raw/lf/home, handler_json, group_json) equals any trusted_hash, so what the Codex hash covers is undetermined. The owed post-deploy check (Task 5) settles it. Repo, stable and cache script hashes are identical, and hooks.json is identical between repo and cache (cache_version=1.2.0+codex.20261017222253).

### Step 4: pre-plan staged set

Empty (git diff --cached --name-status printed nothing, staged=0).

### Step 4: baseline hooks suite

45 rc lines (42 tests/test-*.sh + 3 named files). Baseline failing set: none (all rc=0).

### Re-execution after plan amendment (2026-10-17)

The plan was amended mid-Task 3 (roots test case 5) and reloaded, which reset every task to pending. The first run's evidence is archived in `/private/tmp/claude-502/dh-run1/`. Task 1 Steps 1, 3 and 4 were not re-run: by then Tasks 2 and 3 were applied and staged, so a re-run would have overwritten the before-change baseline with after-change values. Step 2 was re-run into `t1-recheck-trusted.log` and `t1-recheck-groups.log`. Both are byte-identical to the first run (`diff` rc=0), so the trusted hashes and group indexes are unchanged.

## After change

### Task 2: writer log (R1)
- RED (`dh-run1/t2-red.log`): `Results: 3 pass, 7 fail`; FAIL W1, W2, W3, W5, W6, W7, W8.
- GREEN: `test-execution-drift-writers.sh` `Results: 10 pass, 0 fail`. Regression files all rc=0 except `test-execution-drift-roots.sh` case 5 on the re-execution run, which the Task 3 code caused (see Task 3).

### Task 3: other sessions' files excluded (R2-R5)
- RED (`dh-run1/t3-red.log`): `Results: 47 pass, 5 fail`; FAIL cases 43, 44, 45, 46, 49.
- The first GREEN run failed `test-execution-drift-roots.sh` case 5. That check hard-coded one `LC_ALL=C comm -23` in the hook, and the foreign-path subtraction adds a second one, also under `LC_ALL=C`. The operator approved a plan amendment ("Amend plan + fix check (Recommended)"): case 5 now asserts that every `comm` call runs under `LC_ALL=C` and that at least one exists. On a mutant hook with `LC_ALL=C` removed from line 472, the counts are comm=4 and under_C=3, so the check fails as it should.
- GREEN: drift-check `52 pass, 0 fail`; identity `13 pass, 0 fail`; roots `8 pass, 0 fail`; writers `10 pass, 0 fail`.

### Task 4: Codex primary-checkout gap (R7)
- RED: `Results: 3 pass, 4 fail`; FAIL C1, C2, C4, C7 (counts=21).
- GREEN: `Results: 7 pass, 0 fail`. Regression files all rc=0, including `test-managed-worktree-guard.sh` and `test-guard-security.sh`.
- **The fix.** Before it, a Codex session with no managed-worktree assignment sent `apply_patch` and `exec_command` past every write gate. The adapter returned before mapping the native names, and the gates match only Claude names, so both fell through to "tool not handled" (allowed) in any stage and outside allowed_files. The guard now maps `exec_command` to Bash and `apply_patch` to MultiEdit whether or not an assignment exists, and it checks every patch target.
- **Behaviour change in a primary checkout:**
  - Codex mutating shell commands are now blocked outside executing, as Bash is.
  - A patch target that is an absolute path outside the cwd, or that escapes it, is now blocked.

### Task 5: full suite and invariants
- Full hooks suite: 47 rc lines, all rc=0. The Task 1 baseline failing set was empty, so there are no regressions.
- `hooks.json` is byte-identical to HEAD (`hooks_json=0`).
- The real `~/.claude` has no writer log (`real_log=1`) and no test ledgers (`find` printed nothing).

### Owed after deploy (operator-assisted)
After the operator commits and runs `make deploy-hooks`:
1. Open a new Codex session in `/Users/roberthyatt/Code/roleplaying-agents` and record whether a "Hooks need review" prompt appears. Task 1 found no MATCH for any trust-hash candidate, so whether script edits force Codex to re-trust the hooks is still undetermined. This observation settles it.
2. In any session, make one allowed file-tool write. Confirm that `~/.claude/ironclaude-drift-writers.tsv` gains one `epoch<TAB>session<TAB>path` line.
3. Report both results to the operator.
