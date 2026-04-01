#!/bin/bash
# bump-version.sh — Update version in all plugin config files
# Usage: scripts/bump-version.sh 1.0.6
set -euo pipefail

VERSION="${1:-}"

if [ -z "$VERSION" ]; then
  echo "Usage: scripts/bump-version.sh <version>"
  echo "Example: scripts/bump-version.sh 1.0.6"
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required but not installed."
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_JSON="$REPO_ROOT/plugins/ironclaude/.claude-plugin/plugin.json"
MARKETPLACE_JSON="$REPO_ROOT/.claude-plugin/marketplace.json"

# Update plugin.json
TMP=$(mktemp)
jq --arg v "$VERSION" '.version = $v' "$PLUGIN_JSON" > "$TMP" && mv "$TMP" "$PLUGIN_JSON"
echo "✓ plugin.json → $VERSION"

# Update marketplace.json
TMP=$(mktemp)
jq --arg v "$VERSION" '.plugins[0].version = $v' "$MARKETPLACE_JSON" > "$TMP" && mv "$TMP" "$MARKETPLACE_JSON"
echo "✓ marketplace.json → $VERSION"

echo ""
echo "Version bumped to $VERSION. Don't forget to:"
echo "  git add plugins/ironclaude/.claude-plugin/plugin.json .claude-plugin/marketplace.json"
