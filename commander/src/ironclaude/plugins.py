# src/ironclaude/plugins.py
"""Plugin registry and discovery for IronClaude daemon."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import traceback
from typing import Callable

logger = logging.getLogger("ironclaude.plugins")


class PluginRegistry:
    """Registry for plugin commands, event types, and lifecycle hooks."""

    def __init__(self):
        self._commands: dict[str, tuple[str, Callable, Callable]] = {}  # name -> (help, parser, handler)
        self._event_types: dict[str, Callable] = {}  # event_type -> handler
        self._lifecycle_hooks: dict[str, list[Callable]] = {"init": [], "shutdown": []}
        self._preprocessors: list[Callable] = []

    def register_command(self, name: str, help_text: str, parser_fn: Callable, handler_fn: Callable):
        """Register a Slack command. parser_fn(text) -> dict|None, handler_fn(daemon, parsed)."""
        if name in self._commands:
            logger.warning("Plugin overwrites command: %s", name)
        self._commands[name] = (help_text, parser_fn, handler_fn)

    def register_event_type(self, event_type: str, handler_fn: Callable):
        """Register a handler for a queue item type. handler_fn(daemon, item)."""
        self._event_types[event_type] = handler_fn

    def register_lifecycle_hook(self, phase: str, hook_fn: Callable):
        """Register a lifecycle hook. phase is 'init' or 'shutdown'. hook_fn(daemon)."""
        if phase not in self._lifecycle_hooks:
            self._lifecycle_hooks[phase] = []
        self._lifecycle_hooks[phase].append(hook_fn)

    def register_preprocessor(self, preprocessor_fn: Callable):
        """Register an event preprocessor. preprocessor_fn(event, say, daemon) -> dict|None."""
        self._preprocessors.append(preprocessor_fn)

    def get_slash_commands(self) -> dict[str, str]:
        """Return {name: help_text} for all plugin commands."""
        return {name: help_text for name, (help_text, _, _) in self._commands.items()}

    def parse_command(self, text: str) -> dict | None:
        """Try each plugin parser. First non-None result wins."""
        for _, (_, parser_fn, _) in self._commands.items():
            result = parser_fn(text)
            if result is not None:
                return result
        return None

    def handle_command(self, daemon, cmd_type: str, parsed: dict) -> bool:
        """Dispatch to plugin command handler. Returns True if handled."""
        for name, (_, _, handler_fn) in self._commands.items():
            if name.replace("-", "_") == cmd_type:
                handler_fn(daemon, parsed)
                return True
        return False

    def handle_event(self, daemon, item: dict) -> bool:
        """Dispatch to plugin event handler. Returns True if handled."""
        event_type = item.get("type", "")
        if event_type in self._event_types:
            self._event_types[event_type](daemon, item)
            return True
        return False

    def preprocess_event(self, event: dict, say, daemon) -> dict | None:
        """Run preprocessors. First non-None result wins."""
        for preprocessor_fn in self._preprocessors:
            result = preprocessor_fn(event, say, daemon)
            if result is not None:
                return result
        return None

    def run_lifecycle(self, phase: str, daemon):
        """Call all hooks for the given phase."""
        for hook_fn in self._lifecycle_hooks.get(phase, []):
            hook_fn(daemon)


def discover_plugins(registry: PluginRegistry, plugin_dirs: list[str] | None = None) -> list[str]:
    """Scan plugin directories, import plugin.py from each, call register().

    Each entry in plugin_dirs is a path to a potential plugin directory.
    If plugin_dirs is None, uses default locations resolved from this file's path.
    Returns list of loaded plugin names. Logs failures with tracebacks.
    """
    if plugin_dirs is None:
        # Resolve relative to this file: commander/src/ironclaude/plugins.py
        # repo_root = commander/
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        builtin_dir = os.path.join(repo_root, "src", "ironclaude", "plugins")
        plugin_dirs = []
        # Scan builtin plugins dir for subdirectories
        if os.path.isdir(builtin_dir):
            for entry in sorted(os.listdir(builtin_dir)):
                subdir = os.path.join(builtin_dir, entry)
                if os.path.isdir(subdir):
                    plugin_dirs.append(subdir)

    loaded = []
    for plugin_dir in plugin_dirs:
        if not os.path.isdir(plugin_dir):
            logger.info("Plugin directory does not exist: %s", plugin_dir)
            continue
        plugin_file = os.path.join(plugin_dir, "plugin.py")
        if not os.path.isfile(plugin_file):
            continue
        plugin_name = os.path.basename(plugin_dir)
        try:
            # Add parent dir to sys.path temporarily for imports within the plugin
            parent_dir = os.path.dirname(plugin_dir)
            added_to_path = False
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
                added_to_path = True
            try:
                spec = importlib.util.spec_from_file_location(f"plugin_{plugin_name}", plugin_file)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            finally:
                if added_to_path:
                    sys.path.remove(parent_dir)
            if not hasattr(mod, "register"):
                logger.warning("Plugin %s has no register() function, skipping", plugin_name)
                continue
            mod.register(registry)
            loaded.append(plugin_name)
            logger.info("Loaded plugin: %s", plugin_name)
        except Exception:
            logger.error("Failed to load plugin %s:\n%s", plugin_name, traceback.format_exc())
    return loaded
