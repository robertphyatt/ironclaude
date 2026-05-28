---
name: adversarial-review
description: Conduct structured adversarial code reviews with mandatory verification
---

# Adversarial Review

## Purpose

Conduct adversarial code reviews with mandatory verification. This skill guides a single reviewer through 5 sequential phases: Orientation, Deep Read, Verification, Cross-cutting Analysis, and Report. The output is a structured findings document with severity-classified, evidence-backed findings.

This skill standardizes what each individual reviewer does. Multi-worker orchestration (splitting a codebase into domains, spawning N workers, coordinating coverage) is the Brain's responsibility, not the skill's.

## When to Use

- Brain dispatches a worker with a file list for adversarial review
- User manually invokes `/adversarial-review` for a standalone review
- Security audit of a specific subsystem
- Pre-release quality assessment of a domain

<HARD-GATE>
BLIND REVIEW — NON-NEGOTIABLE

The reviewer MUST NOT:
- Search for prior review documents or findings files
- Use git blame to identify recent fixes
- Reference issue trackers or bug reports
- Ask about known issues or prior review results
- Mention what was "fixed" or "improved" from a previous review

Evaluate the code AS IT STANDS NOW. The skill's input contract enforces this
structurally — no fix history parameter exists. If you find yourself thinking
"this was probably fixed from a prior review," STOP — that thought violates
blind review. You do not know what was reviewed before. You are seeing this
code for the first time.
</HARD-GATE>

## Process

### Phase 0: Parse Arguments

**Step 0: Validate and parse input parameters**

Parse the following from skill arguments:

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--files` | YES | — | Comma-separated list of files to review |
| `--scope` | YES | — | Human-readable domain label (e.g., "Core Game Loop", "UI/UX Domain") |
| `--criteria` | NO | `broad` | Review criteria preset: `broad` or `security` |
| `--output` | NO | `docs/reviews/YYYY-MM-DD-<scope-slug>-findings.md` | Output file path |
| `--probes` | NO | — | Comma-separated list of specific questions to investigate |

**Validate:**
1. `--files` is present and non-empty
2. `--scope` is present and non-empty
3. Each file in `--files` exists (use Glob to verify)
4. `--criteria` is either `broad` or `security` if provided

If validation fails, display:
```
BLOCKED: Invalid arguments.

Usage: /adversarial-review --files="file1.gd,file2.gd" --scope="Domain Name" [--criteria=broad|security] [--output=path] [--probes="question1,question2"]

Missing: <list what's missing>
```

Then STOP.

**Display:**
```
Adversarial Review: <scope>
Files: <N> files
Criteria: <broad|security>
Output: <output path>
Probes: <N probes | none>
```

**Criteria presets expand to:**
- `broad` — Broken code, integration bugs, immersion breakers, DRY violations, clumsy architecture, data flow issues, dead code, error handling failures
- `security` — SQL injection, path traversal, command injection, input validation, hardcoded secrets, permissions/access control, concurrency race conditions, information disclosure

### Phase 1: Orientation

**Goal:** Build a mental model of the domain. No findings yet.

**Step 1: Scan the file list**

For each file:
- Note the language, framework, and approximate size
- Identify imports and dependencies on other files in the scope
- Note exports, public APIs, and entry points

**Step 2: Build dependency map**

Construct the dependency order across the scoped files:
- Which files import from which other files in the scope?
- Which files are leaf dependencies (imported but don't import others)?
- Which files are entry points (import others but aren't imported)?

**Step 3: Determine read order**

Read order: dependency leaves first, then consumers. This ensures you understand the contract before seeing the caller.

Display:
```
Orientation complete.
Read order: <ordered file list with rationale>
Dependencies: <brief map>
Entry points: <list>
```

### Phase 2: Deep Read

**Goal:** Read every file thoroughly. Capture all potential findings. Do NOT verify yet.

**Step 4: Sequential deep read**

For each file in the dependency order from Phase 1:
1. Read the entire file
2. Evaluate against the criteria preset
3. Record raw findings as discovered:
   - File path and line range
   - What looks wrong
   - Suspected impact
   - Suspected severity
4. Track cross-references: note when a caller assumes behavior that the callee doesn't guarantee

**What to look for (broad criteria):**
- Code that crashes, loses data, or breaks core functionality
- Type mismatches between caller and callee
- Silent error swallowing (catch-and-ignore, return default on failure)
- Dead code or unreachable branches
- DRY violations (same logic duplicated across files)
- Stale documentation that contradicts implementation
- Debug prints or TODO comments left in production code
- Missing cleanup (signal disconnections, resource release)
- Data flow gaps (information written but never read, or read from wrong source)

**What to look for (security criteria):**
- String interpolation in SQL/command construction
- Unvalidated user input reaching sensitive operations
- Path traversal via unsanitized file paths
- Hardcoded credentials, API keys, or personal paths
- Missing authentication or authorization checks
- Race conditions in concurrent access
- Information disclosure in error messages
- Dependency vulnerabilities

**Do NOT stop to verify during this phase.** Capture everything — false positives are filtered in Phase 3.

Output: raw findings list (unverified).

### Phase 3: Verification

<HARD-GATE>
VERIFICATION GATE — MANDATORY

Every finding that appears in the Phase 5 report MUST pass verification here.
No exceptions. No "this is obviously true so I'll skip verification."

For EACH raw finding from Phase 2:
1. Grep or read the exact file and line range to confirm the code exists as described
2. Read surrounding context — is there a mitigating factor? A guard upstream?
   A comment explaining intentional behavior?
3. If the finding CANNOT be confirmed with evidence: DROP IT
4. If the finding IS confirmed: attach the evidence (exact code snippet from grep/read)

A finding without verification evidence is not a finding — it is a guess.
Guesses do not belong in the report.
</HARD-GATE>

**Step 5: Verify each raw finding**

For each raw finding:

1. **Grep/read** the exact location:
   ```
   Read file.gd lines N-M to confirm code matches description
   ```

2. **Check for mitigating factors:**
   - Is there a guard upstream that prevents the problematic path?
   - Does a comment explain intentional behavior?
   - Is there error handling elsewhere that catches this case?

3. **Classify the result:**
   - **CONFIRMED** — code exists as described, impact is real. Attach the exact code snippet as evidence.
   - **FALSE POSITIVE** — mitigating factor found. Drop the finding. Note why it was dropped (for your own tracking, not for the report).
   - **NEEDS MORE CONTEXT** — read additional files to determine. If still unresolvable after reading, drop it.

4. **Assign final severity** to confirmed findings:
   - **Critical** — Crashes, data loss, security exploits, core functionality broken NOW
   - **Important** — Incorrect behavior visible to users, data flow errors, safety violations
   - **Quality** — Maintainability, DRY violations, dead code, architecture issues
   - **Informational** — Debug prints, documentation gaps, minor concerns

   For `security` criteria: Critical→HIGH, Important→MEDIUM, Quality/Informational→LOW.

Output: verified findings list, each with attached evidence. Track count of dropped false positives.

### Phase 4: Cross-cutting Analysis

**Goal:** Identify systemic patterns across verified findings. Resolve targeted probes.

**Step 6: Identify systemic patterns**

Scan the verified findings for patterns — the same bug class appearing across multiple files:

- Same anti-pattern repeated (e.g., "deferred-quit misuse" affecting 4 findings)
- Same missing check across multiple files (e.g., "null guard without fatal" in 6 locations)
- Same data flow gap across a pipeline (e.g., "palette stored but ignored at render time")

For each pattern:
- **Name it** — descriptive, reusable label
- **List affected finding IDs**
- **Explain why it's systemic** — not just coincidence, but a shared root cause or misunderstanding

**Step 7: Resolve targeted probes (if --probes provided)**

For each probe question:
1. Investigate by reading relevant code
2. Record the result: **RESOLVED** (found the answer), **PASS** (verified correct behavior), or **FAIL** (found a problem — create a finding)
3. Add to the probe results table

### Phase 5: Report

**Goal:** Write the structured findings document.

**Step 8: Write the findings document**

Write the report to the `--output` path (default: `docs/reviews/YYYY-MM-DD-<scope-slug>-findings.md`).

Use this exact structure:

```markdown
# Adversarial Review: <Scope Label>

> **Date:** YYYY-MM-DD
> **Scope:** N files — `file1.gd`, `file2.gd`, ...
> **Criteria:** <broad | security> — <expanded criteria description>
> **Method:** <how files were read — e.g., "Sequential single-context read in dependency order. Cross-reference map built across all files.">

---

## Systemic Pattern: <Pattern Name>

<Description of the pattern, why it's systemic, how it manifests.
List which finding IDs below are affected.>

---

## Critical (Game-Breaking)

### <ID> — `path/to/file.gd:line-range` — <Short title>

<Description of the issue with impact analysis. What breaks, under what conditions.>

```<lang>
<exact code snippet from verification — not paraphrased>
```

<Why this is problematic. Trace the impact chain — show what happens downstream.>

---

## Important (Immersion-Breaking / Significant Bugs)

[Same finding format as Critical]

## Quality (DRY / Architecture)

[Same finding format as Critical]

## Informational

[Same finding format as Critical]

## Targeted Probe Results

| Probe | Status |
|-------|--------|
| <Question investigated> | **RESOLVED** — <what was found> |
| <Question investigated> | **PASS** — <verification evidence> |

---

## Summary

**Total findings: N** (X critical, Y important, Z quality, W informational)

**Highest-priority fixes:**
1. `<ID>` (<file>) — <one-line reason this is highest priority>
2. ...
```

**If no systemic patterns found:** Omit the Systemic Pattern section entirely.
**If no probes provided:** Omit the Targeted Probe Results section entirely.
**If a severity tier has no findings:** Omit that tier's section entirely.

### Finding ID Convention

- Prefix derived from scope or file cluster — e.g., `UI-01`, `S3`, `CM1`, `CC-P2`
- Pick meaningful prefixes based on file groupings within the scope
- IDs are unique and sequential per prefix within the scope
- Do not use severity-based prefixes (H1, M2) — IDs should be stable if severity is reclassified

## Severity Tier Definitions

| Tier | Definition | Threshold |
|------|-----------|-----------|
| Critical | Crashes, data loss, security exploits, core functionality broken | Something is broken or exploitable NOW — not "could be a problem someday" |
| Important | Incorrect behavior visible to users, data flow errors, safety violations | Users experience wrong behavior under realistic conditions |
| Quality | Maintainability, DRY violations, dead code, architecture issues | Code works but is harder to maintain, extend, or reason about |
| Informational | Debug prints, documentation gaps, minor concerns | Worth noting but no behavioral impact |

For `security` criteria mode: Critical→HIGH, Important→MEDIUM, Quality/Informational→LOW. Same definitions, different labels.

## Quality Gates

These are non-negotiable. Every finding in the report must satisfy ALL of:

1. **Verification gate** — The finding was verified in Phase 3 with grep/read evidence. The exact code snippet is attached. No unverified findings in the report.

2. **Impact chain requirement** — Each finding above Informational traces the impact chain. Not "this looks wrong" but "this causes X, which leads to Y, resulting in Z for the user."

3. **Code snippet requirement** — Every finding includes the actual code from the file. Not paraphrased, not summarized — the exact lines as they appear in the source.

4. **No fix history contamination** — The reviewer did not search for prior reviews, git blame recent fixes, or reference issue trackers. The code was evaluated as-is.

5. **Severity calibration** — Severity reflects actual impact, not theoretical risk. Over-classification (calling Quality issues Critical) is a review quality failure.

## Anti-Patterns

Do NOT produce findings that match these patterns:

| Anti-pattern | What it looks like | Why it's bad |
|---|---|---|
| Vague findings | "Error handling could be improved in this file" | No file:line, no code, no impact — actionless |
| Rehashing | "This was noted in a previous review" | Violates blind review — you don't know about previous reviews |
| Severity inflation | Flagging a debug print as Critical | Erodes trust in the severity system |
| No-evidence findings | "This function probably doesn't handle null" | Unverified — should have been dropped in Phase 3 |
| Fix prescriptions without problem statements | "Change line 45 to use X instead of Y" | Identify and evidence the problem. Fix suggestions are optional context, not the finding itself |
| Hypothetical-only risk | "If an attacker could somehow reach this code..." | Critical means broken NOW, not broken in a theoretical attack scenario with no realistic path |

## Key Principles

- **Blind review always** — Evaluate code as it stands. No prior review context, no fix history, no known issues.
- **Verify everything** — No finding enters the report without grep/read evidence. Phase 3 is mandatory, not optional.
- **Impact over identification** — Tracing the impact chain is what makes a finding actionable. "This is wrong" is not enough — show what breaks.
- **Evidence is the finding** — The code snippet IS the evidence. Without it, there is no finding.
- **Severity means something** — Critical = broken now. Important = users see wrong behavior. Quality = maintainability. Informational = worth noting. Respect the definitions.
- **Systemic patterns matter** — Individual findings are useful; patterns that explain WHY multiple things are broken are more useful.
- **Capture then filter** — Phase 2 captures everything. Phase 3 filters ruthlessly. This prevents both over-reporting (unverified guesses) and under-reporting (stopping to verify mid-read and losing flow).
