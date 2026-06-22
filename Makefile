.PHONY: tailscale-serve-setup deploy-hooks

PLUGIN_CACHE_BASE := $(HOME)/.claude/plugins/cache/ironclaude/ironclaude
# Derive the latest installed plugin-cache version dir at runtime instead of
# hard-coding it. A pinned version desyncs from what's actually installed on
# every release (the new version isn't in the cache until the marketplace
# publishes it), which silently skips the plugin-cache hook copy.
PLUGIN_CACHE_VERSION := $(shell ls -1 "$(PLUGIN_CACHE_BASE)" 2>/dev/null | sort -V | tail -1)
PLUGIN_CACHE_HOOK_DIR := $(PLUGIN_CACHE_BASE)/$(PLUGIN_CACHE_VERSION)/hooks
STABLE_HOOK_DIR := $(HOME)/.claude/ironclaude-hooks

# Deploys ALL worker hooks to the runtime locations (stable dir + plugin cache).
# Run after editing any file in worker/hooks/.
deploy-hooks:
	@mkdir -p "$(STABLE_HOOK_DIR)"
	cp worker/hooks/*.sh "$(STABLE_HOOK_DIR)/"
	@if [ -d "$(PLUGIN_CACHE_HOOK_DIR)" ]; then \
	  cp worker/hooks/*.sh "$(PLUGIN_CACHE_HOOK_DIR)/"; \
	  echo "Deployed to plugin cache $(PLUGIN_CACHE_HOOK_DIR)"; \
	else \
	  echo "WARN: plugin cache $(PLUGIN_CACHE_HOOK_DIR) absent — stable dir updated only"; \
	fi
	@echo "Deployed $$(ls worker/hooks/*.sh | wc -l | tr -d ' ') hooks to $(STABLE_HOOK_DIR)"


# Path to music_review directory (machine-specific — not committed).
# Override: make tailscale-serve-setup MUSIC_REVIEW_DIR=/path/to/music_review
MUSIC_REVIEW_DIR ?= $(error MUSIC_REVIEW_DIR is not set — run: make tailscale-serve-setup MUSIC_REVIEW_DIR=/path/to/music_review)

# Configures Tailscale path-based routing for three local services.
# Idempotent — resets all routes before reapplying.
# Run once after OS reinstall or after `tailscale serve reset`.
tailscale-serve-setup:
	tailscale serve reset
	tailscale serve --bg --set-path /wiki http://localhost:8091
	tailscale serve --bg --set-path /music $(MUSIC_REVIEW_DIR)
	tailscale serve --bg http://localhost:8090
	tailscale serve status
