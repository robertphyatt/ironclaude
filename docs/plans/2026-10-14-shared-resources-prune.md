# Prune Committed Music Paths from Shared Resources Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use ironclaude:executing-plans to implement this plan task-by-task.

**Goal:** Remove the 20 now-committed music paths from the roleplaying-agents shared-resource config, and record before/after evidence.

**Requirements:** `docs/plans/2026-10-14-shared-resources-prune-design.md` (§Requirements R1–R5)

**Architecture:** One filter pass: the original config minus 20 exact lines goes to a scratch file. Verify it, then copy it over the config. `.git/info/exclude` stays untouched. The config is read only when a worktree is materialized (`workspace-service.ts:363-368`).

**Tech Stack:** grep, wc, diff, cp.

Loop: full — git/worktree config edit.

## Execution invariants

- Shell state does not persist between steps, so every path is a literal absolute path.
- No `2>/dev/null`. Each step echoes its exit code.
- The only write to the config is Step 5's `cp`. Everything else writes under `/private/tmp/shared-prune/`.
- Any mismatch with an expected value means stop and report. Never widen the pattern list.
- `docs/` is gitignored, so stage with `git -C /Users/roberthyatt/Code/ironclaude add -f`.

Measured during planning (2026-10-14): the config has 66 lines, and an exact-line match on the 20 paths counts 20.

---

## Task 1: Prune and record

No tests required: this is an operational config edit, not code. Verification is the line counts, the diff and a per-path absence check.

**Files:**
- Create: `docs/reviews/2026-10-14-shared-resources-prune-findings.md`
- Stage: the design, plan and plan.json, plus the findings note

**Step 1: Before snapshot + pattern file**

```bash
mkdir -p /private/tmp/shared-prune && cp /Users/roberthyatt/Code/roleplaying-agents/.git/info/worktree-shared-resources /private/tmp/shared-prune/before.txt; echo "snap=$?"; printf '%s\n' 'godot/artificial-adventures/tools/music_retrieval/role_audio_hybrid.py' 'godot/artificial-adventures/tools/music_retrieval/semantic_role_hybrid.py' 'godot/artificial-adventures/tools/music_retrieval/musical_role_profiles.json' 'godot/artificial-adventures/tools/music_retrieval/editorial_top2_development.json' 'godot/artificial-adventures/tools/music_retrieval/__init__.py' 'godot/artificial-adventures/tools/music_retrieval/native_clap_adapter.py' 'godot/artificial-adventures/tools/music_retrieval/native_clap_install.py' 'godot/artificial-adventures/tools/music_retrieval/native_clap_protocol.py' 'godot/artificial-adventures/tools/music_retrieval/clap_index.py' 'godot/artificial-adventures/tools/music_retrieval/native_whole_track.py' 'godot/artificial-adventures/tools/music_retrieval/native_full_catalog.py' 'godot/artificial-adventures/tools/music_retrieval/pilot_schema.py' 'godot/artificial-adventures/tools/music_retrieval/model_install.py' 'godot/artificial-adventures/tools/music_retrieval/native_clap_requirements.lock' 'godot/artificial-adventures/tools/music_retrieval/musical_intent_texts.json' 'godot/artificial-adventures/tools/music_retrieval/binary_artifacts.py' 'godot/artificial-adventures/tools/music_retrieval/native_clap_worker.py' 'godot/artificial-adventures/tools/music_retrieval/full_catalog.py' 'godot/artificial-adventures/audio/music/Call It Cooperation.ogg' 'godot/artificial-adventures/audio/music/Neglected.ogg' > /private/tmp/shared-prune/remove.txt; echo "pat=$?"; wc -l /private/tmp/shared-prune/before.txt /private/tmp/shared-prune/remove.txt; grep -c -x -F -f /private/tmp/shared-prune/remove.txt /private/tmp/shared-prune/before.txt; echo "match=$?"
```

Expected: `snap=0`, `pat=0`, before.txt 66, remove.txt 20, match count `20`, `match=0`. Anything else: stop and report. A count below 20 means a path is missing; above 20 means a duplicate.

**Step 2: Build the filtered candidate**

```bash
grep -v -x -F -f /private/tmp/shared-prune/remove.txt /private/tmp/shared-prune/before.txt > /private/tmp/shared-prune/candidate.txt; echo "filter=$?"; wc -l /private/tmp/shared-prune/candidate.txt
```

Expected: `filter=0`; candidate.txt 46.

**Step 3: Verify the candidate diff**

```bash
diff /private/tmp/shared-prune/before.txt /private/tmp/shared-prune/candidate.txt > /private/tmp/shared-prune/candidate.diff; echo "diff=$?"; grep -c -e '^<' /private/tmp/shared-prune/candidate.diff; grep -c -e '^>' /private/tmp/shared-prune/candidate.diff; echo "added=$?"
```

Expected: `diff=1`, `<` count `20`, `>` count `0`, `added=1`. This check fails if the filter dropped an extra line or rewrote one.

**Step 4: Confirm the candidate keeps the exclude-adjacent and model entries**

```bash
grep -c -x -F -f /private/tmp/shared-prune/remove.txt /private/tmp/shared-prune/candidate.txt; echo "left=$?"; grep -x -F -e 'godot/artificial-adventures/models/music_clap' -e '.venv' /private/tmp/shared-prune/candidate.txt
```

Expected: count `0`, `left=1`, and both `godot/artificial-adventures/models/music_clap` and `.venv` printed.

**Step 5: Apply**

```bash
cp /private/tmp/shared-prune/candidate.txt /Users/roberthyatt/Code/roleplaying-agents/.git/info/worktree-shared-resources; echo "apply=$?"
```

Expected: `apply=0`. If the guard blocks the write, stop and report. Don't work around it.

**Step 6: After snapshot + final diff + exclude untouched**

```bash
cp /Users/roberthyatt/Code/roleplaying-agents/.git/info/worktree-shared-resources /private/tmp/shared-prune/after.txt; echo "after=$?"; wc -l /private/tmp/shared-prune/after.txt; cmp /private/tmp/shared-prune/candidate.txt /private/tmp/shared-prune/after.txt; echo "cmp=$?"; grep -c -x -F -e '/godot/artificial-adventures/tools/music_retrieval/model_install.py' -e '/godot/artificial-adventures/audio/music/Neglected.ogg' /Users/roberthyatt/Code/roleplaying-agents/.git/info/exclude
```

Expected: `after=0`, after.txt 46, `cmp=0`, and an exclude count of `2`, showing the exclude entries are still present.

**Step 7: Findings note**

Write `docs/reviews/2026-10-14-shared-resources-prune-findings.md`. Contents:
- the operator go;
- before and after line counts;
- the 20 removed paths, quoted from `candidate.diff`;
- the `cmp` result and the exclude check;
- why exclude was kept (worktree 3106ccc6 has symlinks at all 20 paths);
- backlog: remove those exclude lines when 3106ccc6 closes;
- deferred: the 019f7d26 close-out goes through `/reconcile` in Codex once main reaches f9c4882.

**Step 8: Stage**

```bash
git -C /Users/roberthyatt/Code/ironclaude add -f docs/reviews/2026-10-14-shared-resources-prune-findings.md docs/plans/2026-10-14-shared-resources-prune-design.md docs/plans/2026-10-14-shared-resources-prune.md docs/plans/2026-10-14-shared-resources-prune.plan.json
```

Expected: no output.
