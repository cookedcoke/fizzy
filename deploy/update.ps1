# Update Fizzy on a Windows home PC (PowerShell). Same as deploy/update.sh:
#   .\deploy\update.ps1            update now
#   .\deploy\update.ps1 -Check     just say whether there's anything new
#   .\deploy\update.ps1 -Watch     keep running: update when the owner presses Update in Settings
#   .\deploy\update.ps1 -Watch -Auto   ...and also update by itself overnight (2am to 5am)
# Pulls, backs up, rebuilds, and checks Fizzy came back; if not, goes back to the old version.
param([switch]$Check, [switch]$Watch, [switch]$Auto)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
# ./updates: Fizzy only drops its "please update" note here. ./update-status: only this script
# writes here; Fizzy reads it through a read-only mount.
$Dir = "update-status"; New-Item -ItemType Directory -Force $Dir, "updates" | Out-Null
$StatusFile = Join-Path $Dir "update-status.json"; $Flag = Join-Path "updates" "update-requested"
$S = @{ state = ""; step = ""; at = ""; from = ""; to = ""; message = ""; behind = 0; started = 0 }
if (Test-Path $StatusFile) { try { (Get-Content $StatusFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $S[$_.Name] = $_.Value } } catch {} }
$Lock = Join-Path $Dir ".lock"   # holds the PID of the updater that's part way through an update
function Lock-Held {
    $p = Get-Content $Lock -ErrorAction SilentlyContinue | Select-Object -First 1
    return ($p -match '^\d+$') -and [bool](Get-Process -Id ([int]$p) -ErrorAction SilentlyContinue)
}

# Sydney time whatever the PC's clock is set to, for overnight updates and the times in Settings.
function Syd { [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, "AUS Eastern Standard Time") }
function Say($m) { Write-Host ("{0}  {1}" -f (Syd).ToString("yyyy-MM-dd HH:mm:ss"), $m) }
function Now { (Syd).ToString("ddd d MMM HH:mm") }
function Now-Unix { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }   # Get-Date -UFormat %s is local time on Windows PowerShell
function Write-Text($path, $text) {  # UTF-8 without the BOM Windows PowerShell adds (Python's json can't read that)
    [IO.File]::WriteAllText((Join-Path (Get-Location) $path), $text, (New-Object Text.UTF8Encoding $false))
}
function Write-Status {
    $S.seen = Now-Unix
    Write-Text "$StatusFile.tmp" ($S | ConvertTo-Json -Compress)
    Move-Item -Force "$StatusFile.tmp" $StatusFile
}
function Fetch {
    git fetch --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { Say "Couldn't reach GitHub."; return $false }
    $S.behind = [int](git rev-list --count "HEAD..@{u}"); return $true
}
function Healthy {  # up to 3 minutes, but give up if the container crashes
    $tries = if ($env:UPDATE_HEALTH_TRIES) { [int]$env:UPDATE_HEALTH_TRIES } else { 90 }
    for ($i = 0; $i -lt $tries; $i++) {
        docker compose exec -T fizzy python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $true }
        $state = (docker compose ps -a --format "{{.State}}" fizzy 2>$null)
        if ($state -match "^(exited|dead)") { return $false }
        Start-Sleep 2
    }
    return $false
}
function Save-Logs {  # before rolling back throws the failed container (and its logs) away
    $out = @("Fizzy's last log lines from the failed update ($((Syd).ToString('yyyy-MM-dd HH:mm'))):")
    $out += (docker compose logs --no-color --tail 40 fizzy 2>&1 | ForEach-Object { "$_" })
    Write-Text (Join-Path $Dir "last-failure.log") ($out -join "`n")
}
# A "running" status with nobody holding the lock means the last update was cut off part way
# (the PC restarted, or the updater was stopped). Say so, or Settings shows "running" for ever.
if ($S.state -eq "running" -and -not (Lock-Held)) {
    $S.state = "failed"; $S.step = "done"; if (-not $S.at) { $S.at = Now }
    $S.message = "The last update was interrupted (the home PC restarted or the updater was stopped). Check Fizzy is on the version you expect, then press Update again."
    Write-Status
}
function Step($name) { $S.step = $name; Write-Status }   # what the progress page shows. Not $s: PowerShell names ignore case, so $s would hide $S
function Build {  # build while the old one keeps serving, then swap (the only time Fizzy is down)
    $env:FIZZY_VERSION = (git rev-parse --short HEAD)
    Step "build"; docker compose build fizzy 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { $script:BuildFailed = $true; return $false }
    Step "restart"; docker compose up -d fizzy 2>$null | Out-Null; return ($LASTEXITCODE -eq 0)
}
function Update {
    if (Lock-Held) { Say "Another update is running."; return }
    Set-Content -Path $Lock -Value $PID
    try { Update-Inner } finally { Remove-Item -Force $Lock -ErrorAction SilentlyContinue }
}
function Update-Inner {
    Remove-Item -Force (Join-Path $Dir "last-failure.log") -ErrorAction SilentlyContinue  # so an early failure doesn't show old logs
    $S.started = Now-Unix; $S.state = "running"; $S.message = ""; Step "check"
    git diff --quiet; $dirty = $LASTEXITCODE -ne 0
    git diff --cached --quiet; if ($LASTEXITCODE -ne 0) { $dirty = $true }
    if ($dirty) { $S.state = "failed"; $S.at = Now; $S.message = "Files in the Fizzy folder were changed by hand, so nothing was pulled. Undo them (git status) and try again."; Step "done"; Say $S.message; return }
    if (-not (Fetch)) { $S.state = "failed"; $S.at = Now; $S.message = "Couldn't reach GitHub."; Step "done"; return }
    $S.from = (git rev-parse --short HEAD)
    if ($S.behind -eq 0) { $S.state = "current"; $S.at = Now; $S.to = $S.from; $S.message = ""; Step "done"; Say "Already up to date ($($S.from))."; return }
    $S.at = Now; $S.message = "Updating from $($S.from)"
    Say "Backing up first..."; Step "backup"
    docker compose exec -T -u fizzy fizzy python -m fizzy backup /backups 2>$null | Out-Null
    $old = (git rev-parse HEAD)
    Step "pull"
    git merge --ff-only --quiet "@{u}"
    if ($LASTEXITCODE -ne 0) { $S.state = "failed"; $S.message = "Couldn't move to the new version (the branch has local commits)."; Step "done"; Say $S.message; return }
    $S.to = (git rev-parse --short HEAD)
    Say "Building $($S.to) ($($S.behind) new change(s))..."
    $script:BuildFailed = $false
    if ((Build) -and ((Step "verify") -or $true) -and (Healthy)) {
        docker compose up -d cloudflared *> $null  # a new cloudflared version in docker-compose.yml (no-op if unchanged)
        $S.state = "ok"; $S.behind = 0; $S.message = "$($S.from) to $($S.to)"; Step "done"; Say "Updated: $($S.message)"; return }
    Write-Text (Join-Path $Dir "bad-version") (git rev-parse "@{u}")  # overnight updates skip this one
    if ($script:BuildFailed) {
        # Nothing was swapped: up -d only runs after a good build, so the old one is still serving.
        git reset --hard --quiet $old
        $S.state = "failed"; $S.to = $S.from; $S.message = "The new version didn't build, so Fizzy stayed on $($S.from)."
        Fetch | Out-Null; Step "done"; Say $S.message; return
    }
    Say "The new version didn't start. Going back to $($S.from)..."
    Save-Logs; Step "rollback"
    git reset --hard --quiet $old; $env:FIZZY_VERSION = (git rev-parse --short HEAD)
    docker compose up -d --build fizzy 2>$null | Out-Null
    $back = ($LASTEXITCODE -eq 0) -and (Healthy)
    $S.state = "failed"; $S.to = $S.from
    if ($back) { $S.message = "The new version didn't start, so it went back to $($S.from). Its last log lines are below." }
    else { $S.message = "The new version didn't start, and going back to $($S.from) didn't work either: Fizzy is down. On the home PC: docker compose logs fizzy" }
    Fetch | Out-Null; Step "done"; Say $S.message
}

if ($Check) { if (Fetch) { Say "$($S.behind) new change(s)." }; Write-Status }
elseif ($Watch) {
    Say ("Watching for updates" + ($(if ($Auto) { " (and updating by itself overnight)" } else { "" })) + ". Ctrl+C to stop.")
    $n = 0
    $me = (git hash-object $PSCommandPath)
    while ($true) {
        # An update can bring a new version of this script: restart into it, so it's never stale.
        if ((git hash-object $PSCommandPath) -ne $me) { Say "The updater itself was updated; restarting it."; & $PSCommandPath @PSBoundParameters; exit }
        # Every few seconds: the Update button. Every 10 minutes: ask GitHub what's new.
        if (Test-Path $Flag) { Remove-Item -Force $Flag; Say "Update requested in Fizzy."; Update }
        elseif ($n % 120 -eq 0) {
            Fetch | Out-Null
            $h = (Syd).Hour
            $bad = Get-Content (Join-Path $Dir "bad-version") -ErrorAction SilentlyContinue
            if ($Auto -and $S.behind -gt 0 -and $h -ge 2 -and $h -lt 5 -and (git rev-parse "@{u}") -ne $bad) { Update }
        }
        if ($n % 12 -eq 0) { Write-Status }   # "still here", for Settings
        $n++; Start-Sleep 5
    }
}
else { Update }
