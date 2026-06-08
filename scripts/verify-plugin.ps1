#requires -Version 5.1
# scripts/verify-plugin.ps1
# End-to-end smoke for the INSTALLED ContextCurator plugin + fresh-shell PATH gate.
# Drives the real cc-* console scripts with stdin JSON events (NO Claude binary needed), exercising
# the full capture -> store -> inject -> working-set loop, then checks cc-mcp liveness. Safe and
# idempotent (uses a throwaway temp $CLAUDE_PROJECT_DIR), so it can be re-run regularly to confirm
# the installed plugin is healthy. Run from a freshly-spawned shell after:
#   uv tool install --editable <checkout> ; uv tool update-shell ; restart.
# Exits nonzero on any failure.
$ErrorActionPreference = 'Stop'

function Fail($msg) { [Console]::Error.WriteLine("VERIFY FAIL: $msg"); exit 1 }

# Feed a hook its stdin event and capture exit code + stdout. PowerShell's `|` mangles the
# encoding of strings sent to a native process' stdin (esp. on 5.1 -> the hook sees an empty/garbled
# event). Writing the event to a UTF-8 (no BOM) temp file and redirecting it via cmd's `<` delivers
# the raw bytes intact and works identically on PS 5.1 and 7.
function Invoke-Hook([string]$exe, [string]$json) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { Fail "$exe does not resolve on PATH." }
    $f = Join-Path $proj ("evt-" + [guid]::NewGuid().ToString('N') + ".json")
    [System.IO.File]::WriteAllText($f, $json, [System.Text.UTF8Encoding]::new($false))
    # Discard the hook's stderr INSIDE cmd (2>nul). Redirecting at the PowerShell level instead
    # trips 5.1's "native stderr under -ErrorAction Stop -> terminating NativeCommandError".
    $out = cmd /c "$exe < `"$f`" 2>nul"
    $code = $LASTEXITCODE
    Remove-Item $f -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Code = $code; Out = ($out -join "`n") }
}

# --- Gate 1: PATH resolution (resolution only; do NOT execute the server -- it has no --help) ---
foreach ($cmd in @('cc-mcp', 'cc-hook-user-prompt', 'cc-hook-post-tool-use')) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "$cmd does not resolve on PATH. Run 'uv tool update-shell' and restart the shell, or use the absolute-shim escape hatch in docs/plugin-install.md."
    }
}
Write-Host "OK: cc-* console scripts resolve on PATH."

$proj = Join-Path $env:TEMP ("cc-verify-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $proj -Force | Out-Null
try {
    $env:CLAUDE_PROJECT_DIR = $proj
    $sid = 'verify'

    # --- Gate 2: capture -- a PostToolUse event stores a chunk in the per-project store ---
    $post = @{ session_id = $sid; tool_name = 'Bash'; call_id = 'c1';
               tool_response = 'sentinel-CCSMOKE authenticate authorize warehouse restock token' } |
            ConvertTo-Json -Compress
    $r = Invoke-Hook 'cc-hook-post-tool-use' $post
    if ($r.Code -ne 0) { Fail "cc-hook-post-tool-use exited $($r.Code)" }
    $db = Join-Path $proj ".context-curator/store.db"
    if (-not (Test-Path $db)) { Fail "store.db not created at $db" }
    Write-Host "OK: cc-hook-post-tool-use captured a chunk -> $db"

    # --- Gate 3: inject -- a UserPromptSubmit event pages the stored chunk back in (the real test) ---
    $prompt = @{ prompt = 'warehouse restock'; session_id = $sid; hook_event_name = 'UserPromptSubmit' } |
              ConvertTo-Json -Compress
    $r = Invoke-Hook 'cc-hook-user-prompt' $prompt
    if ($r.Code -ne 0) { Fail "cc-hook-user-prompt exited $($r.Code)" }
    $rec = Join-Path $proj ".context-curator/decisions/decisions-$sid.jsonl"
    if (-not (Test-Path $rec)) { Fail "no decision record at resolved path $rec (prompt not parsed?)" }
    $last = Get-Content $rec | Where-Object { $_ } | Select-Object -Last 1 | ConvertFrom-Json
    if ([int]$last.working_set_size -lt 1) {
        Fail "capture->inject loop broken: working_set_size=$($last.working_set_size) (nothing injected)"
    }
    if (-not $r.Out) { Fail "cc-hook-user-prompt emitted no inject on stdout despite a stored chunk" }
    Write-Host "OK: cc-hook-user-prompt injected $($last.working_set_size) chunk(s) [$($last.source)] -> $rec"

    # --- Gate 4: cc-mcp process liveness (liveness, not a full JSON-RPC handshake) ---
    $mcp = Start-Process -FilePath 'cc-mcp' -PassThru -NoNewWindow `
            -RedirectStandardError (Join-Path $proj 'mcp.err.log')
    Start-Sleep -Seconds 2
    if ($mcp.HasExited) { Fail "cc-mcp exited prematurely (code $($mcp.ExitCode)); see $proj\mcp.err.log" }
    Stop-Process -Id $mcp.Id -Force
    Write-Host "OK: cc-mcp stayed up (process liveness)."
} finally {
    Remove-Item Env:\CLAUDE_PROJECT_DIR -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $proj -ErrorAction SilentlyContinue
}

Write-Host "`nVERIFY PASS: plugin capture -> inject loop + MCP are healthy."
exit 0
