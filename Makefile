.PHONY: tailscale-serve-setup deploy-hooks

PLUGIN_CACHE_HOOK_DIR := $(HOME)/.claude/plugins/cache/ironclaude/ironclaude/1.0.8/hooks
STABLE_HOOK_DIR := $(HOME)/.claude/ironclaude-hooks

# Deploys updated hooks to both active runtime locations (plugin cache + stable dir).
# Run after editing any file in worker/hooks/.
deploy-hooks:
	cp worker/hooks/episodic-memory-sync.sh $(PLUGIN_CACHE_HOOK_DIR)/episodic-memory-sync.sh
	cp worker/hooks/episodic-memory-sync.sh $(STABLE_HOOK_DIR)/episodic-memory-sync.sh
	@echo "Deployed episodic-memory-sync.sh to plugin cache and stable hooks dir"


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
