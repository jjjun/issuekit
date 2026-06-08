<#
.SYNOPSIS
    Drive the kimi-code CLI non-interactively to implement a plan file.

.DESCRIPTION
    Synchronous agent-driving helper: Claude (or you) writes a plan to a file,
    this wrapper launches kimi headless to implement it, then you review the
    diff before committing. kimi keeps no Claude conversation context, so the
    plan file must be a self-contained spec.

    Safety / robustness baked in:
      - stdin is fed from an empty file so kimi cannot block waiting for input.
      - kimi's -p (headless) mode auto-executes tools, including file writes;
        --auto/-y are rejected in this mode, so there is no approval gate. The
        only safety net is the diff review: kimi is told not to commit or push,
        and you review `git diff` and commit yourself.
      - Output is tee'd to a timestamped log under <Repo>\.kimi-runs\.
      - Hard timeout kills a runaway/hung process.

.EXAMPLE
    .\scripts\run-kimi.ps1 -PlanFile .\plan.md

.EXAMPLE
    .\scripts\run-kimi.ps1 -PlanFile .\docs\issues\active\042-foo.md -TimeoutSec 1200 -Model k2
#>
[CmdletBinding()]
param(
    # Path to the plan / spec file kimi should implement.
    [Parameter(Mandatory = $true)]
    [string] $PlanFile,

    # Repo root kimi operates in. kimi edits files relative to this directory.
    [string] $Repo = (Get-Location).Path,

    # Optional model alias (-m). Empty = kimi config default_model.
    [string] $Model = "",

    # Extra instructions appended to the implementation prompt.
    [string] $Extra = "",

    # Output format passed to kimi (-p mode). stream-json for structured events.
    [ValidateSet("text", "stream-json")]
    [string] $OutputFormat = "text",

    # Hard timeout in seconds before the kimi process is killed.
    [int] $TimeoutSec = 600
)

$ErrorActionPreference = "Stop"

# --- Resolve the kimi executable (it is typically NOT on PATH) ---
$kimiCandidates = @(
    "$env:USERPROFILE\.kimi-code\bin\kimi.exe",
    "C:\Users\jj\.kimi-code\bin\kimi.exe"
)
$kimi = $null
$onPath = Get-Command kimi -ErrorAction SilentlyContinue
if ($onPath) { $kimi = $onPath.Source }
if (-not $kimi) { $kimi = $kimiCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1 }
if (-not $kimi) {
    throw "kimi executable not found. Tried PATH and: $($kimiCandidates -join ', ')"
}

# --- Validate inputs ---
if (-not (Test-Path $PlanFile)) { throw "Plan file not found: $PlanFile" }
if (-not (Test-Path $Repo))     { throw "Repo directory not found: $Repo" }
$planFull = (Resolve-Path $PlanFile).Path
$repoFull = (Resolve-Path $Repo).Path

# --- Build the implementation prompt (plan passed by path, never inlined) ---
# Passing the plan by path avoids the Windows command-line length limit and
# quoting problems that arise from inlining a long spec into -p. The prompt is
# kept single-line; the detailed spec lives in the plan file.
$prompt = "Read the plan file at: $planFull . Implement it fully by editing " +
          "files directly in this repository. Do NOT run git commit or git " +
          "push - leave all changes unstaged for review. If the plan is " +
          "ambiguous, make the most reasonable choice and note it at the end."
if ($Extra) { $prompt = "$prompt $Extra" }

# --- Assemble kimi arguments ---
# NOTE: -p (headless) mode auto-executes tools and REJECTS --auto/-y, so no
# permission flag is passed. The diff review is the safety net.
$kimiArgs = @("-p", $prompt, "--output-format", $OutputFormat)
if ($Model) { $kimiArgs += @("-m", $Model) }

# Start-Process re-joins an -ArgumentList array with bare spaces, which splits
# any argument that itself contains spaces (e.g. the prompt). Build one
# pre-quoted command-line string instead so each argument stays intact.
function Quote-Arg([string] $s) {
    if ($s -match '[\s"]') { '"' + ($s -replace '"', '\"') + '"' } else { $s }
}
$argLine = ($kimiArgs | ForEach-Object { Quote-Arg $_ }) -join ' '

# --- Prepare log + empty stdin file ---
$runDir = Join-Path $repoFull ".kimi-runs"
if (-not (Test-Path $runDir)) { New-Item -ItemType Directory -Path $runDir | Out-Null }
$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$logOut  = Join-Path $runDir "$stamp.out.log"
$logErr  = Join-Path $runDir "$stamp.err.log"
$emptyIn = Join-Path $env:TEMP "kimi-empty-stdin.txt"
if (-not (Test-Path $emptyIn)) { New-Item -ItemType File -Path $emptyIn | Out-Null }

Write-Host "kimi      : $kimi"
Write-Host "repo      : $repoFull"
Write-Host "plan      : $planFull"
Write-Host "mode      : headless -p (auto-exec, no approval gate)  (format=$OutputFormat, timeout=${TimeoutSec}s)"
Write-Host "log       : $logOut"
Write-Host "--- launching kimi ---`n"

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $kimi -ArgumentList $argLine `
    -WorkingDirectory $repoFull -NoNewWindow -PassThru `
    -RedirectStandardInput $emptyIn `
    -RedirectStandardOutput $logOut -RedirectStandardError $logErr
# Touch .Handle so the process object caches the exit code (PowerShell quirk:
# without this, $proc.ExitCode comes back $null after Start-Process -PassThru).
$null = $proc.Handle

if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
    try { $proc.Kill($true) } catch { try { $proc.Kill() } catch {} }
    $sw.Stop()
    Write-Host "`n!!! TIMEOUT after ${TimeoutSec}s - kimi process killed." -ForegroundColor Red
    if (Test-Path $logOut) { Get-Content $logOut -Tail 40 }
    exit 124
}
# Block fully so ExitCode is populated (timed WaitForExit can return before it).
$proc.WaitForExit()
$sw.Stop()
$code = $proc.ExitCode

# --- Surface output ---
if (Test-Path $logOut) { Get-Content $logOut }
if ((Test-Path $logErr) -and (Get-Item $logErr).Length -gt 0) {
    Write-Host "`n--- stderr ---" -ForegroundColor Yellow
    Get-Content $logErr
}

Write-Host "`n--- done: $([math]::Round($sw.Elapsed.TotalSeconds,2))s, exit=$code ---"

# --- Show what changed so you can review before committing ---
Push-Location $repoFull
try {
    if (Test-Path (Join-Path $repoFull ".git")) {
        # status --short also lists new/untracked files (which diff --stat omits).
        Write-Host "`n--- git status --short ---"
        git --no-pager status --short
        Write-Host "`n--- git diff --stat (tracked changes) ---"
        git --no-pager diff --stat
        Write-Host "`nReview with:  git diff (and inspect new files)  |  then commit yourself."
    }
} finally { Pop-Location }

exit $code
