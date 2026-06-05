#requires -Version 5.1
# scripts/verify-plugin.ps1
# Non-interactive M7 plugin verifier + fresh-shell PATH-resolution gate.
# Proves the deterministic surface WITHOUT the Claude binary. Run from a FRESHLY-SPAWNED shell
# (not the dev terminal that already has PATH) after: uv tool install --editable . ; uv tool
# update-shell ; restart. Exits nonzero on any failure.
$ErrorActionPreference = 'Stop'

function Fail($msg) { Write-Error "VERIFY FAIL: $msg"; exit 1 }

# --- Gate 1: PATH resolution (round-3 C1: resolution only, do NOT execute the server) ---
foreach ($cmd in @('cc-mcp', 'cc-hook-user-prompt')) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "$cmd does not resolve on PATH. Run 'uv tool update-shell' and restart the shell, or use the absolute-shim escape hatch in docs/plugin-install.md."
    }
}
Write-Host "OK: cc-mcp and cc-hook-user-prompt resolve on PATH."

# --- Gate 2: the hook runs and writes a decision record at the RESOLVED path ---
$proj = Join-Path $env:TEMP ("cc-verify-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $proj -Force | Out-Null
try {
    $env:CLAUDE_PROJECT_DIR = $proj
    $sid = 'verify'
    $event = '{"prompt":"authenticate authorize user session","session_id":"' + $sid +
             '","hook_event_name":"UserPromptSubmit"}'
    $event | & cc-hook-user-prompt
    if ($LASTEXITCODE -ne 0) { Fail "cc-hook-user-prompt exited $LASTEXITCODE" }

    # Assert the resolved path deterministically (round-1 I4: not "a file appeared").
    $rec = Join-Path $proj ".context-curator/decisions/decisions-$sid.jsonl"
    if (-not (Test-Path $rec)) { Fail "no decision record at resolved path $rec" }
    Write-Host "OK: cc-hook-user-prompt exit 0 and wrote $rec"
} finally {
    Remove-Item Env:\CLAUDE_PROJECT_DIR -ErrorAction SilentlyContinue
}

# --- Gate 3: cc-mcp process liveness (round-2 M2: liveness, not a full handshake) ---
$mcp = Start-Process -FilePath 'cc-mcp' -PassThru -NoNewWindow `
        -RedirectStandardError (Join-Path $proj 'mcp.err.log')
Start-Sleep -Seconds 2
if ($mcp.HasExited) {
    Fail "cc-mcp exited prematurely (code $($mcp.ExitCode)); see $proj\mcp.err.log"
}
Stop-Process -Id $mcp.Id -Force
Write-Host "OK: cc-mcp stayed up (process liveness)."

Remove-Item -Recurse -Force $proj -ErrorAction SilentlyContinue
Write-Host "`nVERIFY PASS: deterministic plugin surface is healthy."
exit 0
