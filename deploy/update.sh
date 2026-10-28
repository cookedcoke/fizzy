#!/usr/bin/env bash
# Update Fizzy on the home PC: pull the latest code, back up, rebuild, and check it came back.
# If the new version doesn't start, it goes back to the old one by itself.
#
#   deploy/update.sh            update now
#   deploy/update.sh --check    just say whether there's anything new
#   deploy/update.sh --watch    keep running: update when the owner presses Update in Settings
#   deploy/update.sh --watch --auto    ...and also update by itself overnight (2am to 5am)
#
# Run it from anywhere; it finds the repo it lives in. Needs git and docker compose.
set -u
ARGS=("$@")
cd "$(dirname "$0")/.." || exit 1
SELF="deploy/update.sh"
# Two folders. ./updates: Fizzy (another user, in the container) only drops its "please update"
# note here; sticky, so it can't touch anything else. ./update-status: only this script writes
# here and Fizzy can only read it (mounted read-only), so the container can never get this
# script to write somewhere it shouldn't.
DIR=update-status
mkdir -p updates "$DIR" && chmod 1777 updates 2>/dev/null; chmod 755 "$DIR" 2>/dev/null
STATUS="$DIR/update-status.json"
FLAG="updates/update-requested"
LOCK="$DIR/.lock"
safe_write() {  # stdin -> $1, via a fresh temp file in the same folder; never through a symlink
    local tmp; tmp=$(mktemp "$DIR/.tmp.XXXXXX") || return 1
    chmod 644 "$tmp"  # mktemp makes it 0600; Fizzy's container runs as another user and has to read it
    cat > "$tmp" && { [ ! -L "$1" ] || rm -f "$1"; } && mv -f "$tmp" "$1"
}

say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
json() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\n\r'; }
STATE=""; AT=""; FROM=""; TO=""; MSG=""; BEHIND=0; STEP=""; STARTED=0
field() { sed -n "s/.*\"$1\": *\"\{0,1\}\([^\",}]*\).*/\1/p" "$STATUS" 2>/dev/null; }
if [ -f "$STATUS" ]; then  # carry on from what was there (this script restarts itself after updating)
    STATE=$(field state); STEP=$(field step); AT=$(field at); FROM=$(field from); TO=$(field to)
    MSG=$(sed -n 's/.*"message": *"\([^"]*\)".*/\1/p' "$STATUS"); STARTED=$(field started); STARTED=${STARTED:-0}
fi
if [ ! -w "$DIR" ]; then
    echo "Can't write to $(pwd)/$DIR. Fix it with: sudo chown -R $(id -un) $DIR updates" >&2; exit 1
fi
write_status() {
    printf '{"state": "%s", "step": "%s", "at": "%s", "from": "%s", "to": "%s", "message": "%s", "behind": %s, "started": %s, "seen": %s}\n' \
        "$(json "$STATE")" "$(json "$STEP")" "$(json "$AT")" "$(json "$FROM")" "$(json "$TO")" "$(json "$MSG")" "${BEHIND:-0}" \
        "${STARTED:-0}" "$(date +%s)" | safe_write "$STATUS"
}

fetch() {
    git fetch --quiet 2>/dev/null || { say "Couldn't reach GitHub."; return 1; }
    BEHIND=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)
}

healthy() {  # Fizzy answers /healthz inside its container: up to 3 minutes, but give up if it crashes
    for _ in $(seq 1 90); do
        docker compose exec -T fizzy python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
            >/dev/null 2>&1 && return 0
        case "$(docker compose ps -a --format '{{.State}}' fizzy 2>/dev/null)" in exited*|dead*) return 1 ;; esac
        sleep 2
    done
    return 1
}

save_logs() {  # before rolling back throws the failed container (and its logs) away
    { echo "Fizzy's last log lines from the failed update ($(date '+%Y-%m-%d %H:%M')):"
      docker compose logs --no-color --tail 40 fizzy 2>&1; } | safe_write "$DIR/last-failure.log"
}

step() { STEP=$1; write_status; }   # what the progress page in Fizzy shows

build() {  # build while the old one keeps serving, then swap (the only time Fizzy is down)
    export FIZZY_VERSION; FIZZY_VERSION="$(git rev-parse --short HEAD)"
    step build; docker compose build fizzy >/dev/null 2>&1 || return 1
    step restart; docker compose up -d fizzy >/dev/null 2>&1
}

update() {
    if ! mkdir "$LOCK" 2>/dev/null; then
        if kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then say "Another update is running."; return 1; fi
        say "Clearing a lock left by an update that was interrupted."; rm -rf "$LOCK"; mkdir "$LOCK" || return 1
    fi
    echo $$ > "$LOCK/pid"
    trap 'rm -rf "$LOCK"' RETURN
    STARTED=$(date +%s); STATE=running; MSG=""; step check
    if ! git diff --quiet || ! git diff --cached --quiet; then
        STATE=failed; AT=$(date '+%a %-d %b %H:%M'); MSG="Files in the Fizzy folder were changed by hand, so nothing was pulled. Undo them (git status) and try again."
        step done; say "$MSG"; return 1
    fi
    fetch || { STATE=failed; AT=$(date '+%a %-d %b %H:%M'); MSG="Couldn't reach GitHub."; step done; return 1; }
    FROM=$(git rev-parse --short HEAD)
    if [ "$BEHIND" = 0 ]; then
        STATE=current; AT=$(date '+%a %-d %b %H:%M'); TO=$FROM; MSG=""; step done; say "Already up to date ($FROM)."; return 0
    fi
    AT=$(date '+%a %-d %b %H:%M'); MSG="Updating from $FROM"
    if ! git merge-base --is-ancestor HEAD @{u}; then
        STATE=failed; MSG="Couldn't move to the new version (the branch has local commits)."; step done; say "$MSG"; return 1
    fi
    say "Backing up first..."; step backup
    docker compose exec -T -u fizzy fizzy python -m fizzy backup /backups >/dev/null 2>&1 || say "(backup skipped: Fizzy wasn't running)"
    old=$(git rev-parse HEAD)
    step pull
    git merge --ff-only --quiet @{u} || { STATE=failed; MSG="Couldn't move to the new version (the branch has local commits)."; step done; say "$MSG"; return 1; }
    TO=$(git rev-parse --short HEAD)
    say "Building $TO ($BEHIND new change(s))..."
    if build && step verify && healthy; then
        STATE=ok; BEHIND=0; MSG="$FROM to $TO"; step done; say "Updated: $MSG"; return 0
    fi
    say "The new version didn't start. Going back to $FROM..."
    save_logs; step rollback
    git reset --hard --quiet "$old"
    export FIZZY_VERSION; FIZZY_VERSION="$(git rev-parse --short HEAD)"
    git rev-parse @{u} | safe_write "$DIR/bad-version"  # overnight updates skip this one; pressing Update still tries it
    if docker compose up -d --build fizzy >/dev/null 2>&1 && healthy; then
        STATE=failed; TO=$FROM; MSG="The new version didn't start, so it went back to $FROM. Its last log lines are below."
    else
        STATE=failed; TO=$FROM; MSG="The new version didn't start, and going back to $FROM didn't work either: Fizzy is down. On the home PC: docker compose logs fizzy"
    fi
    fetch; step done; say "$MSG"; return 1
}

case "${1:-}" in
    --check) fetch && say "$BEHIND new change(s)."; write_status ;;
    --watch)
        auto=""; [ "${2:-}" = "--auto" ] && auto=1
        say "Watching for updates${auto:+ (and updating by itself overnight)}. Ctrl+C to stop."
        n=0
        me=$(git hash-object "$SELF")
        while true; do
            # An update can bring a new version of this script: restart into it, so it's never stale.
            if [ "$(git hash-object "$SELF")" != "$me" ]; then say "The updater itself was updated; restarting it."; exec "$SELF" "${ARGS[@]}"; fi
            # Every few seconds: the Update button. Every 10 minutes: ask GitHub what's new.
            if [ -f "$FLAG" ]; then rm -f "$FLAG"; say "Update requested in Fizzy."; update
            elif [ $((n % 120)) = 0 ]; then
                fetch
                hour=$(date +%H)
                if [ -n "$auto" ] && [ "${BEHIND:-0}" != 0 ] && [ "$hour" -ge 2 ] && [ "$hour" -lt 5 ] \
                   && [ "$(git rev-parse @{u})" != "$(cat "$DIR/bad-version" 2>/dev/null)" ]; then update; fi
            fi
            [ $((n % 12)) = 0 ] && write_status   # "still here", for Settings
            n=$((n + 1)); sleep "${UPDATE_POLL:-5}"
        done ;;
    "") update ;;
    *) sed -n '2,10p' "$0"; exit 2 ;;
esac
