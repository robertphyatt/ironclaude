# IronClaude Commander

AI orchestrator daemon that manages Claude Code workers via Slack.

## Plugins

Plugins extend the daemon with new Slack commands, event handlers, and lifecycle hooks without modifying core files.

### Creating a Plugin

1. Create a directory with a `plugin.py` file
2. Define a `register(registry)` function that registers your hooks
3. Place the directory where the daemon can discover it

**Built-in plugins:** `commander/src/ironclaude/plugins/<name>/plugin.py` (committed)
**Personal plugins:** Any directory at `commander/<name>/plugin.py` (gitignore as needed)

### Hook Types

**Commands** — add Slack slash commands:

```python
def my_parser(text: str) -> dict | None:
    if text.upper() == "GREET":
        return {"type": "greet"}
    return None

def my_handler(daemon, parsed: dict) -> None:
    daemon.slack.post_message("Hello!")

def register(registry):
    registry.register_command("greet", "Say hello", my_parser, my_handler)
```

**Event Preprocessors** — inspect raw Slack events before text parsing:

```python
def my_preprocessor(event: dict, say, daemon) -> dict | None:
    if event.get("some_condition"):
        return {"type": "my_event", "data": event}
    return None  # pass through to normal parsing

def register(registry):
    registry.register_preprocessor(my_preprocessor)
```

**Event Types** — handle custom queue item types:

```python
def my_event_handler(daemon, item: dict) -> None:
    daemon.slack.post_message(f"Got event: {item['data']}")

def register(registry):
    registry.register_event_type("my_event", my_event_handler)
```

**Lifecycle Hooks** — run at daemon init/shutdown:

```python
def on_init(daemon):
    daemon._my_state = {"initialized": True}

def on_shutdown(daemon):
    pass  # cleanup

def register(registry):
    registry.register_lifecycle_hook("init", on_init)
    registry.register_lifecycle_hook("shutdown", on_shutdown)
```

### Plugin State

Plugins store state as attributes on the daemon instance (e.g., `daemon._my_state`). Use a unique prefix to avoid collisions.

### Error Handling

- Failed plugin loads are logged but don't crash the daemon
- Plugin handler exceptions are caught and reported to Slack
- Preprocessor exceptions fall through to normal text parsing
