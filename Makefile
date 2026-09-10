.PHONY: codex-runtime-preflight codex-plugin-install codex-plugin-release tailscale-serve-setup deploy-hooks test test-hooks test-commander test-workspace-manager

PLUGIN_CACHE_BASE := $(HOME)/.claude/plugins/cache/ironclaude/ironclaude
# Derive the latest installed plugin-cache version dir at runtime instead of
# hard-coding it. A pinned version desyncs from what's actually installed on
# every release (the new version isn't in the cache until the marketplace
# publishes it), which silently skips the plugin-cache hook copy.
PLUGIN_CACHE_VERSION := $(shell ls -1 "$(PLUGIN_CACHE_BASE)" 2>/dev/null | sort -V | tail -1)
PLUGIN_CACHE_HOOK_DIR := $(PLUGIN_CACHE_BASE)/$(PLUGIN_CACHE_VERSION)/hooks
STABLE_HOOK_DIR := $(HOME)/.claude/ironclaude-hooks


# Repair Codex's launcher companion before plugin installation or self-update.
# The helper is idempotent and refuses to overwrite any non-equivalent path.
codex-runtime-preflight:
	node worker/scripts/codex-runtime-preflight.mjs --mode repair

# Supported Codex setup entry point. The companion repair cannot be omitted.
codex-plugin-install: codex-runtime-preflight
	codex plugin add ironclaude@ironclaude --json

# Supported source self-update entry point. Ordering is intentional: repair the
# host companion, cachebust, build, validate, then install the same bytes.
codex-plugin-release: codex-runtime-preflight
	python3 $(HOME)/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py worker
	cd worker/mcp-servers/state-manager && npm exec -- tsc --noEmit && npm run bundle
	python3 $(HOME)/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py worker
	codex plugin add ironclaude@ironclaude --json


# ─── Tests ───
# Every suite below was previously reachable only by typing its path by hand:
# nothing in this Makefile, and no CI workflow, invoked the shell guards at all.
# `make test` is the single entry point for contributors and CI.
test: test-hooks test-workspace-manager test-commander

# Runs EVERY hook suite. An earlier version wired only four, which is how a
# guard could regress with `make test-hooks` still green. Each suite exits
# non-zero on failure, so `set -e` semantics come free from make.
test-hooks:
	@for suite in worker/hooks/test-*.sh worker/hooks/tests/test-*.sh commander/hooks/tests/test-*.sh; do \
	  printf '\n--- %s ---\n' "$$suite"; \
	  bash "$$suite" || exit 1; \
	done

test-workspace-manager:
	cd worker/mcp-servers/workspace-manager && npm test

# commander/.venv is gitignored, so a fresh clone has no interpreter there.
# Prefer it when present, otherwise fall back to whatever python3 is on PATH so
# `make test` works for a contributor who just cloned the repo.
test-commander:
	cd commander && PYTHONUNBUFFERED=1 \
	  $$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 ) \
	  -m pytest tests/ -q

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
