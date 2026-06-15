# Brain Model: Fable → Opus Config Change

> **Created:** 2026-06-12
> **Status:** Design Complete

## Summary

Change `brain_model` from `"fable"` to `"opus"` in `config/ironclaude.json`. Fable is currently unavailable per Anthropic announcement. Opus is the default brain model and was the value before D1100 switched it to Fable.

## Architecture

No architectural changes. `config/ironclaude.json` is a minimal runtime config (gitignored, force-staged) containing a single field. The `brain_model` short name is read by `config.py:load_config()`, passed to `claude --model {model}`, with `brain_client.py` appending `[1m]` internally.

## Components

- `config/ironclaude.json` — single file, single field change

## Data Flow

No change to data flow. Model name resolution: `config/ironclaude.json` → `config.py:load_config()` → `brain_client.py` → `claude --model opus`

## Error Handling

No error handling changes needed.

## Testing Strategy

Verify config file contains correct value after edit. Daemon restart (managed separately) will pick up the new model.

## Implementation Notes

- File is gitignored; must use `git add -f` to stage
- Minimal config format established in D1100: only `brain_model` field, no other settings
- Reverting to the pre-D1100 default value
