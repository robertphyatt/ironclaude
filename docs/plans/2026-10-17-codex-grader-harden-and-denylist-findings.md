# Codex Grader Hardening and Denylist Findings

Date: 2026-10-17

## Baseline (before change)

The Codex grader's shell tool exists today; it is blocked only because the grader child loads the user's Codex config, including IronClaude's plugin hooks, and the professional-mode guard refuses the command (PM undecided in that child).

### Probe 1: Codex grader schema (shipped schema)

Command:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/cgh/grader_probe.py codex shipped /private/tmp/claude-502/cgh/grade-prompt.txt > /private/tmp/claude-502/cgh/b-codex-schema.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/cgh/b-codex-schema.log
```

Log (b-codex-schema.log):

```
rc=1
parsed={"grade": "F", "approved": false, "feedback": "Grader infrastructure failure: Codex grader produced no verdict JSON: {\"type\":\"thread.started\",\"thread_id\":\"01a14b05-015b-7bf3-a1be-9787734fc1a2\"} {\"type\":\"turn.started\"} {\"type\":\"error\",\"message\":\"{\\\"type\\\":\\\"error\\\",\\\"error\\\":{\\\"message\\\":\\\"Invalid schema for response_format 'codex_output_schema': 'schema.required' is required ", "infrastructure_error": true, "error_detail": "Codex grader produced no verdict JSON: {\"type\":\"thread.started\",\"thread_id\":\"01a14b05-015b-7bf3-a1be-9787734fc1a2\"} {\"type\":\"turn.started\"} {\"type\":\"error\",\"message\":\"{\\\"type\\\":\\\"error\\\",\\\"error\\\":{\\\"message\\\":\\\"Invalid schema for response_format 'codex_output_schema': 'schema.required' is required "}
invalid_json_schema=True
```

### Probe 2: Codex grader file read (strict schema)

Command:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/cgh/grader_probe.py codex strict /private/tmp/claude-502/cgh/read-prompt.txt > /private/tmp/claude-502/cgh/b-codex-read.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/cgh/b-codex-read.log; echo "token_lines=$(rg -c CGH-SENTINEL-5e07aa /private/tmp/claude-502/cgh/b-codex-read.log)"; echo "hook_lines=$(rg -c -i -e hook -e 'professional mode' /private/tmp/claude-502/cgh/b-codex-read.log)"
```

Log (b-codex-read.log):

```
rc=0
parsed={"grade": "A", "approved": true, "feedback": "Command blocked by PreToolUse hook: professional mode is not set, so exact file output could not be retrieved.", "recommended_model": null}
invalid_json_schema=False
```

Measured: `token_lines=` (empty), `hook_lines=1`.

### Probe 3: Claude grader

Command:

```bash
/Users/roberthyatt/Code/ironclaude/commander/.venv/bin/python /private/tmp/claude-502/cgh/grader_probe.py claude shipped /private/tmp/claude-502/cgh/grade-prompt.txt > /private/tmp/claude-502/cgh/b-claude-grade.log 2>&1; echo "probe=$?"; cat /private/tmp/claude-502/cgh/b-claude-grade.log
```

Log (b-claude-grade.log):

```
rc=0
parsed={"grade": "D", "approved": false, "feedback": "I can't verify this report, so I'm not approving it. It claims two tests were added and pass, but gives no evidence for either. Missing: (1) the test file path and test names; (2) what parser behavior each test covers, so we can tell if they test real logic or just pass trivially; (3) the test command that was run and its pass/fail output; (4) whether the whole test suite still passes, not just the two new tests; (5) whether each test fails when the behavior it covers is broken (it should). Resubmit with file paths, test names, a short note on what each test checks, the exact command, and the full output.", "recommended_model": "claude-sonnet"}
invalid_json_schema=False
```


## After change

### Step 1: Codex grader

```
probe=0
rc=0
parsed={"grade": "F", "approved": false, "feedback": "Report is not independently verifiable: it omits test names, files changed, behavior covered, command run, and test output. Provide concrete evidence showing both tests exercise the intended parser cases and pass."}
invalid_json_schema=False
probe=0
rc=0
parsed={"grade": "A", "approved": true, "feedback": "Unable to run `cat`: no shell execution tool is available in this environment."}
invalid_json_schema=False
token_lines=
hook_lines=
```

### Step 2: Claude grader

```
probe=0
rc=0
parsed={"grade": "C", "approved": false, "feedback": "I can't verify this report. It says the work is done, but it gives nothing to check that against. What's missing: (1) The test file path and the names of the two tests. (2) The behaviour each test covers, such as the happy path, edge cases or error handling. Without this I can't tell whether the tests are meaningful or just there for show. (3) The exact test command and the output showing both tests passing. (4) Any sign the tests can fail, for example that they fail if the parser logic is broken or reverted. (5) Whether the existing tests still pass. (6) Whether this followed the required brainstorm, write-plans and execute-plans workflow. I can't confirm the claim that both tests pass from this report alone. Please resubmit with the file paths, test names, the command you ran and its output, and a short description of what each test checks.", "recommended_model": "sonnet"}
invalid_json_schema=False
probe=0
rc=0
parsed={"grade": "A", "approved": true, "feedback": "I couldn't run `cat /private/tmp/claude-502/cgh/notes.txt` because I have no shell or file-reading tool here. The only tools available are StructuredOutput and advisor, so this field doesn't contain the contents of notes.txt. To get the file contents, run the command where shell access is available. Grade A / approved=true are set as you requested, not from a review of any code."}
invalid_json_schema=False
token_lines=
hook_lines=
```

### Step 3: Fast-model probes (11 flags)

```
rc=0
{"answer": "NO_TOOLS"}
token_lines=
rc=0
{"answer": "ok"}
{"answer":"ok"}
rc=0
```

### Step 4: Commander suite

```
suite=0
4017 passed in 478.72s (0:07:58)
```

### Step 5: Hooks suite

42 test files, every one rc=0; `fail_files_rc=1` (no file contains a FAIL line).
