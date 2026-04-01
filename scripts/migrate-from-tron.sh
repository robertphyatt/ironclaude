#!/usr/bin/env bash
set -euo pipefail
# Usage: ./migrate-from-tron.sh [tron_db_path] [tron_mcp_path]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IRONCLAUDE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IC_DB_DIR="$IRONCLAUDE_DIR/commander/data/db"
TRON_DB="${1:-$HOME/Code/claude-tron/data/db/tron.db}"
IC_DB="$IC_DB_DIR/ic.db"

echo "=== ironclaude migration from claude-tron ==="
echo ""

# 1. Create destination directories
echo "Creating destination directories..."
mkdir -p ~/.ironclaude/brain
mkdir -p ~/.ironclaude/grader
mkdir -p "$IC_DB_DIR"
echo "  ✓ ~/.ironclaude/brain"
echo "  ✓ ~/.ironclaude/grader"
echo "  ✓ $IC_DB_DIR"
echo ""

# 2. Copy tron database
echo "Copying database..."
if [ ! -f "$TRON_DB" ]; then
  echo "  ✗ ERROR: tron.db not found at $TRON_DB"
  exit 1
fi
echo "  Checkpointing WAL..."
sqlite3 "$TRON_DB" "PRAGMA wal_checkpoint(TRUNCATE);"
cp "$TRON_DB" "$IC_DB"
echo "  ✓ $TRON_DB → $IC_DB"
echo ""

# 3. Copy brain working directory state
echo "Copying brain state..."
if [ -f ~/.tron/brain/CLAUDE.md ]; then
  cp ~/.tron/brain/CLAUDE.md ~/.ironclaude/brain/CLAUDE.md
  echo "  ✓ ~/.tron/brain/CLAUDE.md → ~/.ironclaude/brain/CLAUDE.md"
else
  echo "  - ~/.tron/brain/CLAUDE.md not found, skipped"
fi

if [ -d ~/.tron/brain/.claude ]; then
  cp -R ~/.tron/brain/.claude/ ~/.ironclaude/brain/.claude/
  echo "  ✓ ~/.tron/brain/.claude/ → ~/.ironclaude/brain/.claude/"
else
  echo "  - ~/.tron/brain/.claude/ not found, skipped"
fi
echo ""

# 4. Copy grader state
echo "Copying grader state..."
if [ -d ~/.tron/grader ]; then
  cp -R ~/.tron/grader/ ~/.ironclaude/grader/
  echo "  ✓ ~/.tron/grader/ → ~/.ironclaude/grader/"
else
  echo "  - ~/.tron/grader/ not found, skipped"
fi
echo ""

# 5. Write QE tool paths to ironclaude's commander/.env
echo "Writing QE tool paths to commander/.env..."
TRON_MCP="${2:-$HOME/Code/claude-tron/commander/src/tron/orchestrator_mcp.py}"
IC_ENV="$IRONCLAUDE_DIR/commander/.env"

if [ -f "$TRON_MCP" ]; then
  DETECTED_LAUNCH_BIN=$(grep -m1 'godot_bin\s*=' "$TRON_MCP" | grep -o '"[^"]*"' | tr -d '"')
  DETECTED_LAUNCH_PATH=$(grep -m1 'game_path\s*=' "$TRON_MCP" | grep -o '"[^"]*"' | tr -d '"')

  if [ -n "$DETECTED_LAUNCH_BIN" ] && [ -n "$DETECTED_LAUNCH_PATH" ]; then
    if grep -q "QE_LAUNCH_BIN" "$IC_ENV" 2>/dev/null; then
      echo "  - QE tool paths already present in $IC_ENV, skipped"
    else
      printf '\n# QE Tools\nQE_LAUNCH_BIN=%s\nQE_LAUNCH_PATH=%s\n' \
        "$DETECTED_LAUNCH_BIN" "$DETECTED_LAUNCH_PATH" >> "$IC_ENV"
      echo "  ✓ QE tool paths written to $IC_ENV"
    fi
  else
    echo "  - Could not detect QE tool paths from tron source, skipped"
  fi
else
  echo "  - Tron orchestrator not found at $TRON_MCP, skipped"
fi
echo ""

echo "=== Migration complete ==="
echo ""
echo "Verify with:"
echo "  sqlite3 $IC_DB .tables"
echo "  ls -la ~/.ironclaude/brain/"
echo "  ls -la ~/.ironclaude/grader/"
echo "  grep QE_LAUNCH_BIN $IRONCLAUDE_DIR/commander/.env"
