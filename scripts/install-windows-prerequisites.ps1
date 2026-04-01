#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs all Windows prerequisites for the IronClaude Claude Code plugin.

.DESCRIPTION
    This script installs:
    - Node.js LTS (with native module build tools)
    - jq (JSON parser for hook scripts)
    - sqlite3 (state management for hooks)
    - Sets PowerShell execution policy for npm

    Run this script in an Administrator PowerShell:
        .\scripts\install-windows-prerequisites.ps1

.NOTES
    A reboot is recommended after running this script to ensure PATH updates
    are picked up by all applications.
#>

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-CommandExists {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

# ── Step 1: Chocolatey ──────────────────────────────────────────────────
Write-Step "Checking for Chocolatey..."

if (Test-CommandExists "choco") {
    Write-Host "  Chocolatey is already installed." -ForegroundColor Green
} else {
    Write-Step "Installing Chocolatey..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

    # Refresh PATH for this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# ── Step 2: Node.js ─────────────────────────────────────────────────────
Write-Step "Checking for Node.js..."

if (Test-CommandExists "node") {
    $nodeVersion = & node --version
    Write-Host "  Node.js $nodeVersion is already installed." -ForegroundColor Green
} else {
    Write-Step "Installing Node.js LTS..."
    choco install nodejs-lts -y
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# ── Step 3: jq ──────────────────────────────────────────────────────────
Write-Step "Checking for jq..."

if (Test-CommandExists "jq") {
    $jqVersion = & jq --version
    Write-Host "  jq $jqVersion is already installed." -ForegroundColor Green
} else {
    Write-Step "Installing jq..."
    choco install jq -y
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# ── Step 4: sqlite3 ─────────────────────────────────────────────────────
Write-Step "Checking for sqlite3..."

if (Test-CommandExists "sqlite3") {
    $sqliteVersion = & sqlite3 --version
    Write-Host "  sqlite3 $sqliteVersion is already installed." -ForegroundColor Green
} else {
    Write-Step "Installing sqlite3..."
    choco install sqlite -y
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# ── Step 5: Visual Studio Build Tools (for native Node modules) ─────────
Write-Step "Checking for Visual Studio Build Tools..."

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$hasBuildTools = $false

if (Test-Path $vswhere) {
    $installs = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
    if ($installs) { $hasBuildTools = $true }
}

if ($hasBuildTools) {
    Write-Host "  Visual Studio Build Tools are already installed." -ForegroundColor Green
} else {
    Write-Step "Installing Visual Studio Build Tools (for better-sqlite3 native compilation)..."
    choco install visualstudio2022-workload-vctools -y
    Write-Host "  Build tools installed." -ForegroundColor Green
}

# ── Step 6: PowerShell execution policy ──────────────────────────────────
Write-Step "Setting PowerShell execution policy..."

$currentPolicy = Get-ExecutionPolicy -Scope CurrentUser
if ($currentPolicy -eq "RemoteSigned" -or $currentPolicy -eq "Unrestricted") {
    Write-Host "  Execution policy is already $currentPolicy." -ForegroundColor Green
} else {
    Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
    Write-Host "  Set to RemoteSigned for current user." -ForegroundColor Green
}

# ── Step 7: Fix CRLF line endings on hook scripts ────────────────────────
Write-Step "Fixing CRLF line endings on hook scripts..."

$hookDirs = Get-ChildItem -Path (Join-Path $env:USERPROFILE ".claude\plugins\cache\ironclaude\ironclaude") -Directory -ErrorAction SilentlyContinue
foreach ($versionDir in $hookDirs) {
    $hooksPath = Join-Path $versionDir.FullName "hooks"
    if (Test-Path $hooksPath) {
        Get-ChildItem -Path $hooksPath -Filter "*.sh" | ForEach-Object {
            $content = [System.IO.File]::ReadAllText($_.FullName)
            if ($content -match "`r`n") {
                $content = $content -replace "`r`n", "`n"
                [System.IO.File]::WriteAllText($_.FullName, $content)
            }
        }
        Write-Host "  Fixed line endings in $hooksPath" -ForegroundColor Green
    }
}

# ── Step 7.5: Set git core.autocrlf ────────────────────────────────────
Write-Step "Setting git core.autocrlf=input..."

$currentAutoCrlf = & git config --global core.autocrlf 2>$null
if ($currentAutoCrlf -eq "input") {
    Write-Host "  git core.autocrlf is already set to 'input'." -ForegroundColor Green
} else {
    & git config --global core.autocrlf input
    Write-Host "  Set git core.autocrlf=input (prevents CRLF conversion on checkout)." -ForegroundColor Green
}

# ── Step 8: Install episodic-memory npm dependencies ────────────────────
Write-Step "Installing episodic-memory npm dependencies..."

$episodicMemoryDir = Join-Path $env:USERPROFILE ".claude\plugins\cache\ironclaude\ironclaude\1.0.0\mcp-servers\episodic-memory"

if (Test-Path $episodicMemoryDir) {
    Push-Location $episodicMemoryDir
    & npm install 2>&1
    Pop-Location
    Write-Host "  Dependencies installed." -ForegroundColor Green
} else {
    Write-Host "  episodic-memory directory not found (install the plugin first, then re-run this script)." -ForegroundColor Yellow
}

# ── Done ─────────────────────────────────────────────────────────────────
Write-Host "`n" -NoNewline
Write-Host "========================================" -ForegroundColor Green
Write-Host "  All prerequisites installed!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  IMPORTANT: Reboot your computer to ensure all PATH" -ForegroundColor Yellow
Write-Host "  changes are picked up by Claude Code." -ForegroundColor Yellow
Write-Host ""

# Verify
Write-Step "Verification:"
if (Test-CommandExists "node")    { Write-Host "  node    $(& node --version)" -ForegroundColor Green }    else { Write-Host "  node    NOT FOUND" -ForegroundColor Red }
if (Test-CommandExists "npm")     { Write-Host "  npm     $(& npm --version)" -ForegroundColor Green }     else { Write-Host "  npm     NOT FOUND" -ForegroundColor Red }
if (Test-CommandExists "jq")      { Write-Host "  jq      $(& jq --version)" -ForegroundColor Green }      else { Write-Host "  jq      NOT FOUND" -ForegroundColor Red }
if (Test-CommandExists "sqlite3") { Write-Host "  sqlite3 $(& sqlite3 --version)" -ForegroundColor Green } else { Write-Host "  sqlite3 NOT FOUND" -ForegroundColor Red }
