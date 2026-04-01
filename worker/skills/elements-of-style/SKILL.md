---
name: elements-of-style
description: Apply writing principles to technical documentation and code
---

# Elements of Style

## Purpose

Apply Strunk & White's writing principles to ALL prose that humans will read: documentation, commit messages, error messages, code comments, API responses, log messages, and user-facing text.

## When to Use

- Writing or reviewing documentation
- Creating commit messages
- Writing error messages
- Drafting user-facing text
- Reviewing pull request descriptions
- Creating code comments

## Core Principles

Adapted from Strunk & White's "The Elements of Style" for technical writing.

### 1. Omit Needless Words

**Bad:**
```
The reason why we need to do this is because the system requires...
```

**Good:**
```
We need this because the system requires...
```

**Application to code:**
- Comments: Say what isn't obvious from code
- Error messages: State problem and solution, nothing more
- Documentation: Be concise - every word must earn its place

### 2. Use Active Voice

**Bad:**
```
The file was deleted by the system.
An error was encountered during processing.
```

**Good:**
```
The system deleted the file.
Processing encountered an error.
```

**Application to code:**
- Commit messages: "Add feature" not "Feature was added"
- Error messages: "Failed to connect" not "Connection could not be established"
- Documentation: "Click the button" not "The button should be clicked"

### 3. Use Definite, Specific, Concrete Language

**Bad:**
```
The function might take some time to complete.
There could be issues with the configuration.
Performance may be impacted.
```

**Good:**
```
The function takes 2-5 seconds to complete.
Invalid JSON in config.yml line 23.
Query time increased from 100ms to 2s.
```

**Application to code:**
- Error messages: Exact file paths and line numbers
- Documentation: Specific examples, not abstract descriptions
- Log messages: Actual values, not vague descriptions

### 4. Put Statements in Positive Form

**Bad:**
```
Do not forget to save your changes.
Authentication did not succeed.
```

**Good:**
```
Remember to save your changes.
Authentication failed.
```

**Application to code:**
- Error messages: "File not found" → "Cannot find file"
- Documentation: "Avoid X" → "Use Y instead"
- API responses: Focus on what to do, not what not to do

### 5. Revise and Rewrite

**Process:**
1. Write first draft
2. Read aloud
3. Remove 20% of words
4. Replace vague words with specific ones
5. Check for passive voice
6. Verify each sentence adds value

**Application to code:**
- Review commit messages before committing
- Edit documentation before publishing
- Simplify error messages through testing
- Refine comments during code review

### 6. No Guessing

**This is a IronClaude addition to Strunk & White principles.**

**Never use speculative language without evidence:**

**Forbidden:**
- "This likely happens because..."
- "The problem is probably..."
- "This might be caused by..."
- "It seems like..."
- "I think..."

**Required:**
```
Evidence: [file.py:123] shows X
Conclusion: Therefore Y occurs
```

**Application to code:**
- Debug output: Facts, not theories
- Error messages: What happened, not what might have happened
- Documentation: Verified behavior, not assumptions
- Code comments: Explain actual behavior, not guesses about intent

### 7. Be Clear, Not Clever

**Bad:**
```
// This is some real voodoo magic
// TODO: Here be dragons
function doTheThing() { ... }
```

**Good:**
```
// Normalize timestamps to UTC before comparison
function normalizeTimestamp(ts) { ... }
```

**Application to code:**
- Variable names: Clear over clever
- Function names: Descriptive over cute
- Comments: Explain why, not joke
- Error messages: Helpful over witty

## Process

When reviewing any written content:

**Step 1: Read it aloud**
- Does it sound natural?
- Are there awkward phrasings?
- Is anything unclear?

**Step 2: Apply the 7 principles**
- Mark needless words → delete them
- Mark passive voice → change to active
- Mark vague language → make specific
- Mark negative statements → rewrite positively
- Mark speculation → require evidence
- Mark clever language → simplify

**Step 3: Verify clarity**
Ask: Can someone with zero context understand this?

**Step 4: Present improvements**
```
Original:
[original text]

Suggested revision:
[improved text]

Changes:
- Removed needless words (15 → 10 words)
- Changed passive to active voice
- Made language more specific
- Removed speculation
```

## Key Principles

- **Every word must earn its place**: If removing it doesn't change meaning, remove it
- **Active voice is stronger**: "System deleted file" > "File was deleted by system"
- **Specific beats vague**: "2 seconds" > "some time"
- **Positive beats negative**: "Authentication failed" > "Authentication did not succeed"
- **Evidence, not guesses**: Never say "likely" or "probably" without proof
- **Clear beats clever**: Good code explains itself

## Examples

### Commit Messages

**Bad:**
```
Updated the authentication system to fix some issues that were found
```

**Good:**
```
Fix authentication timeout after 30 seconds

Root cause: Session token TTL exceeded connection pool timeout
```

### Error Messages

**Bad:**
```
An error occurred while trying to process your request. This might be due to invalid input or a system issue.
```

**Good:**
```
Invalid JSON at line 23, column 5: Expected ',' but found '}'
```

### Documentation

**Bad:**
```
This function is used to validate user input and it checks various things to make sure the data is correct.
```

**Good:**
```
Validates user input against schema constraints:
- Email format (RFC 5322)
- Password strength (min 12 chars, mixed case, symbols)
- Username uniqueness (case-insensitive)
```

### Code Comments

**Bad:**
```
// This is probably needed for backwards compatibility
// TODO: Figure out what this does
const MAGIC_NUMBER = 42;
```

**Good:**
```
// PostgreSQL connection pool size
// Tuned for 100 concurrent users with 30s query timeout
const POOL_SIZE = 42;
```
