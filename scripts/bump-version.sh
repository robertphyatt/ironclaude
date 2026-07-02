#!/bin/bash
# bump-version.sh — Update the IronClaude version across all version-bearing files.
# Usage: scripts/bump-version.sh 1.0.15
#
# Updates the three files kept in lockstep by
# commander/tests/test_version_consistency.py:
#   - commander/pyproject.toml        (TOML `version = "..."`)
#   - worker/.claude-plugin/plugin.json   (`.version`)
#   - .claude-plugin/marketplace.json     (`.plugins[0].version`)
# It does NOT edit CHANGELOG.md or create git tags — do those by hand.
set -euo pipefail

VERSION="${1:-}"

if [ -z "$VERSION" ]; then
  echo "Usage: scripts/bump-version.sh <version>"
  echo "Example: scripts/bump-version.sh 1.0.15"
  exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Error: version must be N.N.N (got '$VERSION')"
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required but not installed."
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYPROJECT="$REPO_ROOT/commander/pyproject.toml"
PLUGIN_JSON="$REPO_ROOT/worker/.claude-plugin/plugin.json"
MARKETPLACE_JSON="$REPO_ROOT/.claude-plugin/marketplace.json"

for f in "$PYPROJECT" "$PLUGIN_JSON" "$MARKETPLACE_JSON"; do
  if [ ! -f "$f" ]; then
    echo "Error: expected version file not found: $f"
    exit 1
  fi
done

# Update commander/pyproject.toml — rewrite the first `version = "..."` line
# (TOML, so jq doesn't apply). awk keeps this portable across BSD/GNU.
TMP=$(mktemp)
awk -v v="$VERSION" '!done && /^version = "/ { sub(/"[^"]*"/, "\"" v "\""); done=1 } { print }' "$PYPROJECT" > "$TMP" && mv "$TMP" "$PYPROJECT"
echo "✓ pyproject.toml → $VERSION"

# Update worker/.claude-plugin/plugin.json
TMP=$(mktemp)
jq --arg v "$VERSION" '.version = $v' "$PLUGIN_JSON" > "$TMP" && mv "$TMP" "$PLUGIN_JSON"
echo "✓ plugin.json → $VERSION"

# Update .claude-plugin/marketplace.json
TMP=$(mktemp)
jq --arg v "$VERSION" '.plugins[0].version = $v' "$MARKETPLACE_JSON" > "$TMP" && mv "$TMP" "$MARKETPLACE_JSON"
echo "✓ marketplace.json → $VERSION"

echo ""
echo "Version bumped to $VERSION. Don't forget to:"
echo "  - add a '## $VERSION' entry to CHANGELOG.md"
echo "  - git add commander/pyproject.toml worker/.claude-plugin/plugin.json .claude-plugin/marketplace.json"
