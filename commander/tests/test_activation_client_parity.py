"""Provider-aware professional-mode activation contract."""
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "worker/skills/activate-professional-mode/SKILL.md"
CODEX_READ_PROGRAM_START = "<!-- CODEX_NATIVE_READ_PROGRAM_START -->"
CODEX_READ_PROGRAM_END = "<!-- CODEX_NATIVE_READ_PROGRAM_END -->"
CANONICAL_CODEX_READ_PROGRAM = """\
var instructionFs = await import("node:fs/promises");
var instructionResult;
try {
  instructionResult = {
    exists: true,
    text: await instructionFs.readFile(<ABSOLUTE_NORMALIZED_PATH>, "utf8"),
  };
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
  instructionResult = { exists: false, text: null };
}
nodeRepl.write(JSON.stringify(instructionResult));
"""

WORKFLOW_REQUIREMENT = (
    "> **WORKFLOW REQUIREMENT (when professional mode is active):** All code changes — "
    "regardless of size or perceived simplicity — MUST follow the brainstorm → write-plans "
    "→ execute-plans workflow. Never suggest, attempt, or agree to circumvent this workflow. "
    'There are no "small" or "trivial" exceptions. If you think a change is too simple for '
    "the workflow, you are wrong — follow it anyway."
)

CANONICAL_SHARED_BODIES = (
    """1. **Challenge Assumptions**
   - Question stated requirements when they seem incomplete or contradictory
   - Ask clarifying questions before accepting assumptions
   - Verify understanding before proceeding""",
    """2. **Verify with Evidence**
   - Don't guess or use probabilistic language without proof
   - Avoid "likely", "probably", "should work" without verification
   - Test claims before stating them as fact""",
    """3. **Refuse Impossible Requests**
   - Clearly state when something cannot be done
   - Explain why it's impossible
   - Suggest alternatives when available""",
    """4. **Persistent Questioning**
   - Keep asking until understanding is complete
   - Don't proceed with unclear requirements
   - Confirm understanding before implementation""",
    """5. **No Premature Optimization**
   - Solve the stated problem, not hypothetical future problems
   - Keep implementations simple and focused
   - Don't add features that weren't requested""",
    """7. **Subagent Discipline**
   - Keep subagent prompts focused: one task, one clear deliverable, no open-ended exploration
   - Use inline execution mode when tasks are complex enough to risk context exhaustion spirals
   - Set max_turns on subagents so they fail fast rather than spiral (compaction loses critical detail, causing re-research loops)
   - Never put orchestration in subagents — state management, code review invocation, flag management, and task sequencing belong in the main context""",
    """8. **No Sycophantic Responses**
   - Never use performative agreement ("Great point!", "You're absolutely right!", "That's a great catch")
   - When corrected by a hook or review, respond with technical reasoning, not agreement
   - If you disagree with review feedback, push back with evidence
   - Before implementing a correction, verify the correction is actually correct
   - Forbidden phrases: "Great point", "You're right", "Good catch", "Absolutely", "That's a great suggestion\"""",
    """10. **No Workflow Avoidance Under Stage/Context Restrictions**
    - Do NOT propose to "checkpoint / bank progress / resume fresh / find a safe stopping point" mid-execution. Plan/task artifacts on disk ARE the checkpoint. Pauses are operator-initiated via `plan-interruption`.
    - Do NOT ask the operator to run read-only queries (sqlite, grep, bash) because the current stage blocks Bash. The correct move is an investigation PM loop whose execute stage unblocks Bash — do it yourself.
    - See `ironclaude:workflow-durability` for the decision table.""",
    """11. **Boy Scout Rule — Leave It Better Than You Found It**
    - Never dismiss an evidence-backed defect because it is pre-existing, adjacent, or outside the immediate change
    - If cleanup is safe, relevant, and within the authorized task scope, fix it through the active workflow and verify the result
    - If cleanup would materially expand scope, change behavior, require destructive action, affect external systems, or require new authority, describe the finding, evidence, proposed cleanup scope, and risk, then ask permission before proceeding
    - If cleanup is blocked or unsafe, record the finding and explain the constraint instead of suppressing it
    - Do not use this rule to justify speculative refactoring or unrequested features""",
)

CANONICAL_CODEX_SEARCH_BODY = """6. **Search Before Guessing**
   - If context feels incomplete (after compaction), search episodic memory
   - Don't make up details - search for them
   - Search with the `episodic-memory` MCP server's search capability"""

CANONICAL_CLAUDE_SEARCH_BODY = """6. **Search Before Guessing**
   - If context feels incomplete (after compaction), search episodic memory
   - Don't make up details - search for them
   - Use the ironclaude:search-conversations agent, not raw MCP tools"""

CANONICAL_CODEX_ADVISOR_BODY = """9. **Advisor Fallback (advisor unavailable ≠ skip the advisor)**
   - Fire the advisor at natural discretionary points: before substantive work, when stuck, and before declaring done
   - Invoke a one-tier-up report-only reviewer with `codex exec -m <one-tier-up-model>` using `luna → terra → sol`; at the `sol` ceiling, run a same-tier blind `sol` pass
   - Reconcile the review with evidence; never proceed unreviewed because an advisor command is unavailable"""

CANONICAL_CLAUDE_ADVISOR_BODY = """9. **Advisor Fallback**
   - When the `advisor` tool returns unavailable, do NOT skip the advisor step or just reason it through yourself
   - Spawn a top-tier subagent via the `Agent` tool (`model=fable` if Fable is available, else `model=opus`) with the same context and a focused, report-only adversarial-review prompt (task, change/decision, evidence, specific questions)
   - Client-aware: that is the Claude path; a Codex session has no `Agent` tool, so it invokes a one-tier-up `codex exec -m <one-up>` review instead (`luna→terra→sol`, `sol` ceiling = same-tier blind) — see the `ironclaude:advisor-fallback` skill
   - Weight its findings as you would the advisor's; "no advisor" means "use a subagent for the same effect," never "proceed unreviewed\""""


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _provider_sections() -> tuple[str, str, str]:
    text = SKILL.read_text()
    codex = _section(
        text,
        "#### Codex: root `AGENTS.md`",
        "#### Claude Code: `CLAUDE.md` and `.claude/rules/behavioral.md`",
    )
    claude = _section(
        text,
        "#### Claude Code: `CLAUDE.md` and `.claude/rules/behavioral.md`",
        "### Step 3.5: Check validation backend",
    )
    return text, codex, claude


def _codex_read_program(text: str) -> str:
    assert text.count(CODEX_READ_PROGRAM_START) == 1
    assert text.count(CODEX_READ_PROGRAM_END) == 1
    bounded = _section(text, CODEX_READ_PROGRAM_START, CODEX_READ_PROGRAM_END)
    prefix = "\n```javascript\n"
    suffix = "```\n"
    assert bounded.startswith(prefix)
    assert bounded.endswith(suffix)
    return bounded[len(prefix) : -len(suffix)]


def _run_codex_read(program: str, path: Path) -> subprocess.CompletedProcess[str]:
    rendered = program.replace(
        "<ABSOLUTE_NORMALIZED_PATH>",
        json.dumps(str(path.resolve())),
    )
    assert "<ABSOLUTE_NORMALIZED_PATH>" not in rendered
    adapter = (
        "globalThis.nodeRepl = "
        "{ write(value) { globalThis.console.log(value); } };\n"
    )
    return subprocess.run(
        ["node", "--input-type=module", "-e", adapter + rendered],
        check=False,
        capture_output=True,
        text=True,
    )


def test_activation_selects_owned_surface_from_trusted_client():
    text, codex, claude = _provider_sections()
    assert '"client": "codex"' in text
    assert '"client": "claude"' in text
    assert "Do not infer the client from instruction-file presence" in text
    assert "Missing or unsupported `client`" in text
    assert "Codex activation must not create or edit `CLAUDE.md`" in codex
    assert "anything under `.claude/rules`" in codex
    assert "Claude activation must not create or edit `AGENTS.md`" in claude


def test_activation_defines_exact_instruction_file_operation_bindings():
    text = SKILL.read_text()
    bindings = _section(
        text,
        "## Instruction-file operation bindings",
        "## When to Use",
    )
    assert "`<READ_INSTRUCTION_FILE>`" in bindings
    assert "`<WRITE_INSTRUCTION_FILE>`" in bindings
    assert "the active client resolves it to its native file operation" in bindings
    assert "that concrete replacement is authoritative" in bindings
    assert "call it directly" in bindings
    assert (
        "Every existence check, semantic read, creation, append or prepend, "
        "and read-back"
    ) in bindings
    assert "Natural-language file verbs are not an alternate execution path" in bindings


def test_activation_defines_concrete_provider_native_file_bindings():
    text = SKILL.read_text()
    bindings = _section(
        text,
        "## Instruction-file operation bindings",
        "## When to Use",
    )
    codex = _section(bindings, "### Codex", "### Claude Code")
    claude = _section(bindings, "### Claude Code", "## End provider bindings")

    assert _codex_read_program(text) == CANONICAL_CODEX_READ_PROGRAM
    assert (
        "`<READ_INSTRUCTION_FILE>` means the marker-bounded `node_repl` `js` "
        "program below"
    ) in codex
    assert (
        "`<WRITE_INSTRUCTION_FILE>` means the native `apply_patch` tool"
        in codex
    )
    assert (
        "Do not use Bash, shell commands, Claude `Read`, or Claude `Write` "
        "as fallback operations."
    ) in codex
    assert "`<READ_INSTRUCTION_FILE>` means the native `Read` tool." in claude
    assert "`<WRITE_INSTRUCTION_FILE>` means the native `Write` tool." in claude
    assert (
        "Do not use Codex `node_repl` or `apply_patch` as fallback operations."
        in claude
    )

    config = _section(
        text,
        "### Step 3.5: Check validation backend",
        "### Step 4: Activate and confirm",
    )
    assert (
        "Use the trusted client's concrete `<READ_INSTRUCTION_FILE>` binding"
        in config
    )


def test_codex_native_read_program_executes_all_file_outcomes(tmp_path: Path):
    program = _codex_read_program(SKILL.read_text())
    present = tmp_path / "present file.md"
    expected_text = "alpha\nβeta\n"
    present.write_text(expected_text, encoding="utf-8")

    present_result = _run_codex_read(program, present)
    assert present_result.returncode == 0, present_result.stderr
    assert json.loads(present_result.stdout) == {
        "exists": True,
        "text": expected_text,
    }

    missing_result = _run_codex_read(program, tmp_path / "missing.md")
    assert missing_result.returncode == 0, missing_result.stderr
    assert json.loads(missing_result.stdout) == {
        "exists": False,
        "text": None,
    }

    error_result = _run_codex_read(program, tmp_path)
    assert error_result.returncode != 0
    assert "EISDIR" in error_result.stderr


def test_all_owned_surface_actions_use_exact_operation_tokens():
    _, codex, claude = _provider_sections()
    for instruction in (
        "Call `<READ_INSTRUCTION_FILE>` for root `AGENTS.md` to perform its existence check and complete semantic read.",
        "Call `<WRITE_INSTRUCTION_FILE>` for root `AGENTS.md` with the complete canonical template.",
        "Call `<WRITE_INSTRUCTION_FILE>` for root `AGENTS.md` with the full computed result after any append or prepend.",
        "Call `<READ_INSTRUCTION_FILE>` for root `AGENTS.md` as the read-back gate.",
    ):
        assert instruction in codex
    for instruction in (
        "Call `<READ_INSTRUCTION_FILE>` for root `CLAUDE.md` and `.claude/rules/behavioral.md` to perform their existence checks and complete semantic reads.",
        "Call `<WRITE_INSTRUCTION_FILE>` for root `CLAUDE.md` with the complete compact index.",
        "Call `<WRITE_INSTRUCTION_FILE>` for `.claude/rules/behavioral.md` with the complete canonical template.",
        "Call `<WRITE_INSTRUCTION_FILE>` for the affected Claude-owned file with the full computed result after any append or prepend.",
        "Call `<READ_INSTRUCTION_FILE>` for each existing Claude-owned instruction file as the read-back gate.",
    ):
        assert instruction in claude
    assert "Read root `AGENTS.md`." not in codex
    assert (
        "Read root `CLAUDE.md` and `.claude/rules/behavioral.md`."
        not in claude
    )


def test_activation_contains_full_independent_canonical_bodies():
    _, codex, claude = _provider_sections()
    assert WORKFLOW_REQUIREMENT in codex
    assert WORKFLOW_REQUIREMENT in claude
    for body in CANONICAL_SHARED_BODIES:
        assert body in codex
        assert body in claude
    assert CANONICAL_CODEX_SEARCH_BODY in codex
    assert CANONICAL_CLAUDE_SEARCH_BODY in claude
    assert CANONICAL_CODEX_ADVISOR_BODY in codex
    assert CANONICAL_CLAUDE_ADVISOR_BODY in claude


def test_activation_binds_state_checks_to_exact_root_session():
    text = SKILL.read_text()
    assert (
        '{"professional_mode": "undecided", "client": "codex", '
        '"session_id": "<provider-native-root-session>"}'
    ) in text
    assert (
        '{"professional_mode": "undecided", "client": "claude", '
        '"session_id": "<provider-native-root-session>"}'
    ) in text
    assert (
        "The response must include `professional_mode`, `client`, and a "
        "nonempty `session_id`."
    ) in text
    assert "Bind this activation to the exact `session_id` returned in Step 1." in text
    verify_only = _section(
        text,
        "#### Verify-only activation",
        "#### Updating activation",
    )
    assert "the same trusted `client` and the same bound `session_id`" in verify_only
    updating = _section(text, "#### Updating activation", "Display:")
    assert "`session_id` equal to the bound Step 1 `session_id`" in updating


def test_semantic_classification_is_conservative():
    text, codex, claude = _provider_sections()
    conservative_rule = (
        "Only affirmative evidence of a concept's full required behavior counts "
        "as covered. Topical similarity, shared keywords, and uncertainty are "
        "uncovered."
    )
    assert conservative_rule in text
    assert "When classification is uncertain, treat the concept as uncovered." in codex
    assert "When classification is uncertain, treat the concept as uncovered." in claude
    assert "treat it as covered rather than duplicating it" not in text
    assert "When uncertain, treat equivalent wording as covered." not in text
    assert (
        "In `verify-only` mode, list every exact uncovered concept, write "
        "nothing, and do not call `set_professional_mode`."
    ) in text


def test_activation_verifies_required_surface_before_enabling_mode():
    text, _, _ = _provider_sections()
    assert (
        "- If it returns `on`: set setup mode to `verify-only` and continue "
        "to Step 3. Do not skip instruction verification."
    ) in text
    assert (
        "In `verify-only` mode, do not write instruction files. If the active "
        "surface is incomplete, report the exact missing semantics and stop."
    ) in text
    verify_at = text.index("**Read-back verification gate**")
    enable_at = text.index(
        'Call `set_professional_mode` with `value: "on"`',
        verify_at,
    )
    assert verify_at < enable_at
    gate = _section(
        text,
        "**Read-back verification gate**",
        "### Step 3.5: Check validation backend",
    )
    assert "Leave professional mode in its prior state" in gate
    assert "Do not call `set_professional_mode` after any setup failure" in gate
    assert "best-effort" not in _section(
        text,
        "### Step 3: Establish the active client's instruction surface",
        "### Step 3.5: Check validation backend",
    )


def test_claude_checks_combined_semantics_before_creating_absent_rules_file():
    _, _, claude = _provider_sections()
    analyze_at = claude.index(
        "First semantically evaluate the existing `CLAUDE.md`"
    )
    decide_at = claude.index(
        "Only if that evaluation finds missing concepts"
    )
    assert analyze_at < decide_at
    assert (
        "Do not create `.claude/rules/behavioral.md` solely because it is absent."
    ) in claude
    assert (
        "If `CLAUDE.md` alone covers the workflow requirement and all eleven "
        "concepts, leave the rules file absent."
    ) in claude


def test_activation_uses_provider_native_state_manager_names_and_advisors():
    text, codex, claude = _provider_sections()
    assert "mcp__plugin_ironclaude_state-manager__get_professional_mode" in text
    assert "mcp__plugin_ironclaude_state-manager__set_professional_mode" in text
    assert "Codex `state-manager` `get_professional_mode`" in text
    assert "Codex `state-manager` `set_professional_mode`" in text
    assert "`codex exec -m <one-tier-up-model>`" in codex
    assert "`luna → terra → sol`" in codex
    assert "`Agent` tool" in claude
    assert "`model=fable`" in claude
    assert "`model=opus`" in claude


def test_activation_confirms_without_redundant_set_when_already_on():
    text = SKILL.read_text()
    verify_only = _section(
        text,
        "#### Verify-only activation",
        "#### Updating activation",
    )
    assert "Do not call `set_professional_mode`" in verify_only
    assert "Call `get_professional_mode` again" in verify_only
    updating = _section(text, "#### Updating activation", "Display:")
    assert 'Call `set_professional_mode` with `value: "on"`' in updating
    assert "Do not call `get_professional_mode` again after a successful set" in updating
    assert "`success: true`" in updating
    assert '`professional_mode: "on"`' in updating
    assert "`previous` equal to the Step 1 prior mode" in updating
    assert "`session_id` equal to the bound Step 1 `session_id`" in updating
    assert "ACTIVATION STATUS UNKNOWN" in updating
    assert "Do not claim that prior state was restored" in updating


# Salvaged from the removed activation certification rig: the one assertion in it
# that read shipped product content. Its strings appear in no other test.
CONCEPT_NAMES = (
    "Challenge Assumptions",
    "Verify with Evidence",
    "Refuse Impossible Requests",
    "Persistent Questioning",
    "No Premature Optimization",
    "Search Before Guessing",
    "Subagent Discipline",
    "No Sycophantic Responses",
    "Advisor Fallback",
    "No Workflow Avoidance Under Stage/Context Restrictions",
    "Boy Scout Rule",
)


def test_activation_source_requires_exact_verify_only_diagnostics():
    source = SKILL.read_text(encoding="utf-8")
    start = source.index("**Exact verify-only diagnostic contract**")
    end = source.index("\nPreserve all unrelated project guidance", start)
    contract = source[start:end]
    assert all(f"- {concept}" in contract for concept in CONCEPT_NAMES)
    assert "enumerated name set must equal the uncovered concept set exactly" in contract
    assert "Do not\nsubstitute a numeric range" in contract
    assert "concepts 1–11" in contract
    assert "behavioral concepts" in contract


def test_codex_surfaces_never_reference_an_unavailable_agent():
    """worker/.codex-plugin/plugin.json declares no agents key, so no codex-facing
    instruction may name ironclaude:search-conversations."""
    _, codex, _ = _provider_sections()
    assert "ironclaude:search-conversations" not in codex
    template = (ROOT / "commander/src/ironclaude/templates/worker_agents.md").read_text()
    assert "ironclaude:search-conversations" not in template
