#!/bin/bash
# Unit tests for the read-only Bash predicates. DB-free.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/../bash-readonly-guard.sh"

fail=0
allow() { if is_readonly_research_bash "$1"; then :; else echo "FAIL expected ALLOW: $1"; fail=1; fi; }
block() { if is_readonly_research_bash "$1"; then echo "FAIL expected BLOCK: $1"; fail=1; fi; }

# Safe read-only -> ALLOW
allow "find . -name '*.log'"
allow "cat /tmp/ic/daemon.log"
allow "ls -la /tmp"
allow "grep -r pattern ."
allow "rg foo src"
allow "head -n 50 file"
allow "tail -n 100 file"
allow "wc -l file"
allow "find . \\( -name a -o -name b \\)"

# Redirection -> BLOCK
block "cat secret > /etc/x"
block "ls >> out"
block "grep x f 2> err"
block "cat a < b"

# find write/exec actions -> BLOCK
block "find . -delete"
block "find . -name x -exec rm {} +"
block "find . -exec rm {} \\;"
block "find . -execdir rm {} +"
block "find . -fprintf out fmt"
block "find . -fls /tmp/x"
block "find . -ok rm {} \\;"

# Chaining / substitution -> BLOCK
block "ls \$(rm x)"
block "ls \`rm x\`"
block "grep x f | sh"
block "ls && rm x"
block "ls ; rm x"
block "cat <(rm x)"

# Newline injection -> BLOCK
block "ls"$'\n'"rm x"

# Non-allowlisted commands -> BLOCK
block "rm -rf /"
block "git commit -m x"
block "sqlite3 db 'UPDATE x'"
block "sed -i s/a/b/ f"
block "echo hi"

if [ "$fail" -eq 0 ]; then echo "ALL PASS"; else echo "FAILURES"; exit 1; fi
