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

echo "=== Migration complete ==="
echo ""
echo "Verify with:"
echo "  sqlite3 $IC_DB .tables"
echo "  ls -la ~/.ironclaude/brain/"
echo "  ls -la ~/.ironclaude/grader/"
