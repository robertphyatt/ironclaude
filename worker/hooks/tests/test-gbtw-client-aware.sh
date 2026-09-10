#!/bin/bash
# test-gbtw-client-aware.sh — asserts get-back-to-work-impl.sh renders its
# Skill-invocation guidance through ic_skill_ref (client-aware) instead of
# hardcoding Claude Code's "Skill tool" wording, so Codex sessions get the
# $ironclaude:<skill> form and Claude sessions keep the Skill(...) form.
set -u

IMPL="/Users/roberthyatt/Code/ironclaude/worker/hooks/get-back-to-work-impl.sh"

pass=0
fail=0

ok() { echo "PASS $1"; pass=$((pass+1)); }
bad() { echo "FAIL $1 — $2"; fail=$((fail+1)); }

# ---------------------------------------------------------------------------
# (1) Behavioral: one subshell per client
# ---------------------------------------------------------------------------
out_codex=$( IC_HOOK_CLIENT=codex GBTW_TEST_MODE=1 bash -c 'source "'"$IMPL"'"; printf "%s\n----\n%s" "$_IC_MSG_CODE_REVIEW_REQUIRED" "$_IC_MSG_GRADE_TOO_LOW"' )

if printf '%s' "$out_codex" | grep -qF '$ironclaude:code-review --task-boundary'; then
    ok "codex/contains-dollar-skill-ref"
else
    bad "codex/contains-dollar-skill-ref" "missing \$ironclaude:code-review --task-boundary"
fi

if printf '%s' "$out_codex" | grep -qF 'Skill(skill='; then
    bad "codex/no-skill-paren-form" "found 'Skill(skill=' in codex output"
else
    ok "codex/no-skill-paren-form"
fi

if printf '%s' "$out_codex" | grep -iqF 'skill tool'; then
    bad "codex/no-skill-tool-wording" "found 'skill tool' (case-insensitive) in codex output"
else
    ok "codex/no-skill-tool-wording"
fi

out_claude=$( IC_HOOK_CLIENT=claude GBTW_TEST_MODE=1 bash -c 'source "'"$IMPL"'"; printf "%s\n----\n%s" "$_IC_MSG_CODE_REVIEW_REQUIRED" "$_IC_MSG_GRADE_TOO_LOW"' )

if printf '%s' "$out_claude" | grep -qF 'Skill(skill="ironclaude:code-review", args="--task-boundary")'; then
    ok "claude/contains-skill-paren-form"
else
    bad "claude/contains-skill-paren-form" 'missing Skill(skill="ironclaude:code-review", args="--task-boundary")'
fi

# ---------------------------------------------------------------------------
# (2) Static: no leftover "skill tool" wording (except the known-good
#     "writing-plans Skill tool" phrase elsewhere in the file, if any)
# ---------------------------------------------------------------------------
n=$(grep -iF 'skill tool' "$IMPL" | grep -vF 'writing-plans Skill tool' | wc -l | tr -d ' ')
if [ "$n" = "0" ]; then
    ok "static/no-skill-tool-wording (count=$n)"
else
    bad "static/no-skill-tool-wording" "count=$n expected=0"
fi

# ---------------------------------------------------------------------------
# (3) Static regression guard: no hardcoded Skill(skill= construction
# ---------------------------------------------------------------------------
n2=$(grep -cF 'Skill(skill=' "$IMPL")
if [ "$n2" = "0" ]; then
    ok "static/no-hardcoded-skill-paren (count=$n2)"
else
    bad "static/no-hardcoded-skill-paren" "count=$n2 expected=0"
fi

# ---------------------------------------------------------------------------
# (4) Positive: ic_skill_ref used for the three workflow skills
# ---------------------------------------------------------------------------
for needle in \
    'ic_skill_ref "ironclaude:brainstorming"' \
    'ic_skill_ref "ironclaude:writing-plans"' \
    'ic_skill_ref "ironclaude:executing-plans"'
do
    if grep -qF -- "$needle" "$IMPL"; then
        ok "positive/$needle"
    else
        bad "positive/$needle" "not found in $IMPL"
    fi
done

echo "---- client-aware: $pass passed, $fail failed ----"
[ "$fail" -eq 0 ]
