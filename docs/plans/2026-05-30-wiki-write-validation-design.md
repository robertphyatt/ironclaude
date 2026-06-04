# Wiki Write Validation Strengthening Design

> **Created:** 2026-05-30
> **Status:** Design Complete

## Summary

`wiki_write` in `commander/src/ironclaude/orchestrator_mcp.py` has three validation gaps that allow non-compliant wiki pages to be created. This change closes all three gaps by adding guard clauses to the existing validation block and updating the test suite accordingly.

## Architecture

Single approach: three sequential guard clauses added to `wiki_write`'s validation block (lines 2545–2556 in `orchestrator_mcp.py`), each returning an error string before any file I/O occurs. All three follow the existing pattern of early-return with a descriptive message.

## Components

### Gap 1 — Directive suffix check

**New guard clause** after the existing `re.match(r'^d\d+', page)` prefix check:

```python
if re.search(r'-d\d{1,4}(?:-|$)', page):
    return (
        f"Invalid page name '{page}': directive-number suffixes (-d<N>) are not allowed. "
        "Wiki pages must be concept-focused, not directive logs. "
        "Use a descriptive name like 'worker-lifecycle' or 'state-update-patterns'."
    )
```

Catches: `sqlite-contention-fix-d681`, `state-update-d884`, `some-feature-d12`. Does not match: `operator-dashboard`, `pf2e-pipeline`, `word-about-domain`.

### Gap 2 — Expanded date check

**Replace** the existing `re.search(r'\d{4}-\d{2}-\d{2}', page)` check with `r'\d{4}-\d{2}'` (superset — catches both YYYY-MM-DD and YYYY-MM).

**Add** a second date guard clause immediately after:

```python
if re.search(r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d{4}', page, re.IGNORECASE):
    return (
        f"Invalid page name '{page}': date-stamped names are not allowed. "
        "Wiki pages are persistent concepts, not log entries. "
        "Use a descriptive name like 'deployment-patterns' or 'rollout-strategy'."
    )
```

Catches: `ideaservice-adversarial-review-2026-05`, `ironclaude-releases-may2026`, `jan2026-retrospective`. Does not match: `worker-lifecycle`, `operator-preferences`.

### Gap 3 — Content minimum check

**New guard clause** after all name checks, before file I/O:

```python
if len(content.strip()) < 50:
    return (
        f"Invalid content for '{page}': content must be at least 50 characters after stripping whitespace. "
        "Placeholder pages are not allowed."
    )
```

Catches: empty strings, whitespace-only strings, strings under 50 chars after strip.

## Data Flow

All three checks are guard clauses returning early — no state is mutated on rejection. Order:
1. Directive prefix check (existing)
2. **Directive suffix check (new)**
3. YYYY-MM-DD/YYYY-MM date check (existing, regex widened)
4. **Month+year date check (new)**
5. Path traversal check (existing)
6. **Content minimum check (new)**
7. File write + index rebuild + git commit (existing)

## Error Handling

All three new validations return descriptive error strings matching the existing pattern. No exceptions raised. Callers receive a string beginning with `"Invalid page name"` or `"Invalid content"`.

## Testing Strategy

### New tests (all in `TestWikiTools`, `test_orchestrator_mcp.py`)

| Test | Input | Expected |
|------|-------|----------|
| `test_wiki_write_rejects_directive_suffix` | `sqlite-contention-fix-d681` | `"directive-number"` in result, no file created |
| `test_wiki_write_rejects_directive_suffix_short` | `state-d12` | `"directive-number"` in result, no file created |
| `test_wiki_write_rejects_year_month_pattern` | `ideaservice-adversarial-review-2026-05` | `"date-stamped"` in result, no file created |
| `test_wiki_write_rejects_month_year_pattern` | `ironclaude-releases-may2026` | `"date-stamped"` in result, no file created |
| `test_wiki_write_rejects_empty_content` | page=`valid-page`, content=`""` | `"content"` in result, no file created |
| `test_wiki_write_rejects_whitespace_content` | page=`valid-page`, content=`"   "` | `"content"` in result, no file created |
| `test_wiki_write_rejects_short_content` | page=`valid-page`, content=`"x" * 49` | `"content"` in result, no file created |
| `test_wiki_write_accepts_minimum_content` | page=`valid-page`, content=`"x" * 50` | page created successfully |

### Existing test fixes

Add `VALID_CONTENT = "A" * 60` as a class constant on `TestWikiTools`. Replace all short content fixture strings (e.g., `"Content."`, `"Some content here."`, `"Alpha content."`) throughout the class with `VALID_CONTENT`. Approximately 15 test methods affected.

## Implementation Notes

- `re.IGNORECASE` on the month+year check handles hypothetical mixed-case page names without requiring page name normalization.
- The YYYY-MM regex (`r'\d{4}-\d{2}'`) is a superset of the existing YYYY-MM-DD check. The existing check can be simplified to this pattern alone — the separate YYYY-MM-DD check is no longer needed.
- The 50-char minimum applies after `content.strip()`, so whitespace-only content fails regardless of length.
- Test fixture updates are purely mechanical — no test logic changes, only the string literals passed as `content`.
