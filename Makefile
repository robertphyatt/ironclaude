.PHONY: tailscale-serve-setup

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
