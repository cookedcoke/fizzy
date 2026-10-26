# Update Fizzy on a Windows home PC (PowerShell). Same as deploy/update.sh:
#   .\deploy\update.ps1            update now
#   .\deploy\update.ps1 -Check     just say whether there's anything new
#   .\deploy\update.ps1 -Watch     keep running: update when the owner presses Update in Settings
#   .\deploy\update.ps1 -Watch -Auto   ...and also update by itself overnight (2am to 5am)
# Pulls, backs up, rebuilds, and checks Fizzy came back; if not, goes back to the old version.
param([switch]$Check, [switch]$Watch, [switch]$Auto)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Dir = "updates"; New-Item -ItemType Directory -Force $Dir | Out-Null
$StatusFile = Join-Path $Dir "update-status.json"; $Flag = Join-Path $Dir "update-requested"
$S = @{ state = ""; at = ""; from = ""; to = ""; message = ""; behind = 0 }
if (Test-Path $StatusFile) { try { (Get-Content $StatusFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $S[$_.Name] = $_.Value } } catch {} }

function Say($m) { Write-Host ("{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) }
function Now { Get-Date -Format "ddd d MMM HH:mm" }
function Write-Status {
    $S.seen = [int][double]::Parse((Get-Date -UFormat %s))
    ($S | ConvertTo-Json -Compress) | Set-Content -Encoding utf8 "$StatusFile.tmp"
    Move-Item -Force "$StatusFile.tmp" $StatusFile
}
function Fetch {
    git fetch --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { Say "Couldn't reach GitHub."; return $false }
    $S.behind = [int](git rev-list --count "HEAD..@{u}"); return $true
}
function Healthy {
    for ($i = 0; $i -lt 45; $i++) {
        docker compose exec -T fizzy python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep 2
    }
    return $false
}
function Build { $env:FIZZY_VERSION = (git rev-parse --short HEAD); docker compose up -d --build fizzy 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) }
function Update {
    git diff --quiet; $dirty = $LASTEXITCODE -ne 0
    if ($dirty) { $S.state = "failed"; $S.at = Now; $S.message = "Files in the Fizzy folder were changed by hand, so nothing was pulled. Undo them (git status) and try again."; Write-Status; Say $S.message; return }
    if (-not (Fetch)) { $S.state = "failed"; $S.at = Now; $S.message = "Couldn't reach GitHub."; Write-Status; return }
    $S.from = (git rev-parse --short HEAD)
    if ($S.behind -eq 0) { $S.state = "current"; $S.at = Now; $S.to = $S.from; $S.message = ""; Write-Status; Say "Already up to date ($($S.from))."; return }
    $S.state = "running"; $S.at = Now; $S.message = "Updating from $($S.from)"; Write-Status
    Say "Backing up first..."
    docker compose exec -T -u fizzy fizzy python -m fizzy backup /backups 2>$null | Out-Null
    $old = (git rev-parse HEAD)
    git merge --ff-only --quiet "@{u}"
    if ($LASTEXITCODE -ne 0) { $S.state = "failed"; $S.message = "Couldn't move to the new version (the branch has local commits)."; Write-Status; Say $S.message; return }
    $S.to = (git rev-parse --short HEAD)
    Say "Building $($S.to) ($($S.behind) new change(s))..."
    if ((Build) -and (Healthy)) { $S.state = "ok"; $S.behind = 0; $S.message = "$($S.from) to $($S.to)"; Write-Status; Say "Updated: $($S.message)"; return }
    Say "The new version didn't start. Going back to $($S.from)..."
    git reset --hard --quiet $old; Build | Out-Null; Healthy | Out-Null
    (git rev-parse "@{u}") | Set-Content (Join-Path $Dir "bad-version")  # overnight updates skip this one
    $S.state = "failed"; $S.to = $S.from; $S.message = "The new version didn't start, so it went back to $($S.from). Check: docker compose logs fizzy"
    Fetch | Out-Null; Write-Status; Say $S.message
}

if ($Check) { if (Fetch) { Say "$($S.behind) new change(s)." }; Write-Status }
elseif ($Watch) {
    Say ("Watching for updates" + ($(if ($Auto) { " (and updating by itself overnight)" } else { "" })) + ". Ctrl+C to stop.")
    $n = 0
    while ($true) {
        if (Test-Path $Flag) { Remove-Item -Force $Flag; Say "Update requested in Fizzy."; Update }
        elseif ($n % 10 -eq 0) {
            Fetch | Out-Null
            $h = (Get-Date).Hour
            $bad = Get-Content (Join-Path $Dir "bad-version") -ErrorAction SilentlyContinue
            if ($Auto -and $S.behind -gt 0 -and $h -ge 2 -and $h -lt 5 -and (git rev-parse "@{u}") -ne $bad) { Update }
        }
        Write-Status; $n++; Start-Sleep 60
    }
}
else { Update }
