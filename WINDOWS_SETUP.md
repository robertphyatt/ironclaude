# Windows Setup Guide

This guide covers installing and configuring IronClaude on Windows. For the quick-start installation, run the automated installer in an Administrator PowerShell:

```powershell
.\scripts\install-windows-prerequisites.ps1
```

If you prefer manual installation or need to troubleshoot, follow the sections below.

---

## Prerequisites

IronClaude requires the following tools on Windows:

| Tool | Purpose | Install Command |
|------|---------|-----------------|
| [Chocolatey](https://chocolatey.org/) | Package manager | See [chocolatey.org/install](https://chocolatey.org/install) |
| Node.js 20+ | MCP servers | `choco install nodejs-lts -y` |
| jq | JSON parsing in hooks | `choco install jq -y` |
| sqlite3 | State management | `choco install sqlite -y` |
| VS Build Tools | Native module compilation | `choco install visualstudio2022-workload-vctools -y` |

**Alternative (winget):**
```powershell
winget install OpenJS.NodeJS.LTS
winget install jqlang.jq
winget install SQLite.SQLite
```

After installing, reboot to ensure PATH updates are picked up by all applications.

---

## Bash Environment

IronClaude's hooks are bash scripts. **Launch Claude Code from Git Bash**, not PowerShell or Command Prompt.

Git Bash (included with [Git for Windows](https://gitforwindows.org/)) provides the MSYS2/MinGW environment that hooks require. IronClaude's `hook-logger.sh` automatically bootstraps the PATH for Git Bash:

```bash
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
  export PATH="/mingw64/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
fi
```

**WSL** works but is a separate Linux installation -- not "Windows support." If you use WSL, follow the Linux instructions in the main README instead.

**PowerShell alone is insufficient.** Hook scripts will not execute in PowerShell. You must use Git Bash or a compatible MSYS2 environment.

---

## Path Handling

IronClaude uses **forward slashes** for all paths, on all platforms.

### Convention

- All paths in hook scripts, configuration files, and plan JSON use forward slashes (`/`)
- Never use backslashes (`\`) in paths passed to bash scripts or plan `allowed_files`
- Backslashes in `allowed_files` will cause PM guard file-access checks to fail silently

### Automatic Conversion

`hook-logger.sh` provides `normalize_path()` which converts Windows backslashes to forward slashes automatically for tool input from Claude Code:

```bash
normalize_path() {
  echo "${1//\\//}"
}
```

Every hook calls `normalize_path` on `FILE_PATH` immediately after parsing it from JSON input.

### Script Directory Resolution

The canonical pattern for resolving the current script's directory (works in both Git Bash and native bash):

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
```

All hook scripts use this pattern. It resolves symlinks and produces an absolute path with forward slashes.

---

## CRLF Line Endings

Git on Windows may check out `.sh` files with CRLF line endings, which causes `bash: \r: command not found` errors.

**Automated fix:** The `install-windows-prerequisites.ps1` script fixes this automatically (Step 7).

**Manual fix:**
```bash
sed -i 's/\r$//' ~/.claude/plugins/cache/ironclaude/ironclaude/*/hooks/*.sh
```

**Prevent recurrence:**
```bash
git config --global core.autocrlf input
```

This tells Git to convert CRLF to LF on commit but not convert LF to CRLF on checkout.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| MCP server won't start | Build failed or dependencies missing | Check `~/.claude/ironclaude-mcp-state-manager.log` and `~/.claude/ironclaude-mcp-episodic-memory.log` for diagnostic output |
| Hooks fail silently | jq or sqlite3 not in PATH | Run `command -v jq && command -v sqlite3` in Git Bash. Install missing tools with `choco install jq sqlite -y` |
| Statusline shows `?.?.?` | jq not available | Install jq: `choco install jq -y`. Check `~/.claude/ironclaude-statusline-errors.log` for details |
| Statusline shows `[!]` marker | One or more statusline components failed | Check `~/.claude/ironclaude-statusline-errors.log` for the specific failure |
| PM guard blocks everything | Session not initialized (sqlite3 missing) | Install sqlite3, restart Claude Code session |
| `better-sqlite3` won't compile | Missing C++ build tools | Install VS Build Tools: `choco install visualstudio2022-workload-vctools -y` |
| `bash: \r: command not found` | CRLF line endings on hook scripts | Run: `sed -i 's/\r$//' ~/.claude/plugins/cache/ironclaude/ironclaude/*/hooks/*.sh` |
| `command -v` not recognized | Running from PowerShell instead of Git Bash | Launch Claude Code from Git Bash, not PowerShell |

### Debug Mode

If you need to edit IronClaude configuration files while professional mode is active (e.g., fixing a broken `settings.json`), enable debug config writes:

```bash
# In Git Bash -- edit or create the config file:
echo '{"debug_allow_config_writes": true}' > ~/.claude/ironclaude-hooks-config.json
```

This allows Edit/Write to files under `~/.claude/` without deactivating professional mode. A warning is logged on every bypassed check. **Disable when done:**

```bash
echo '{"debug_allow_config_writes": false}' > ~/.claude/ironclaude-hooks-config.json
```

### Log Files

| Log File | Contents |
|----------|----------|
| `~/.claude/ironclaude-mcp-state-manager.log` | State manager MCP server startup, build, and runtime logs |
| `~/.claude/ironclaude-mcp-episodic-memory.log` | Episodic memory MCP server startup, build, and runtime logs |
| `~/.claude/ironclaude-statusline-errors.log` | Statusline parsing failures and degradation events |
