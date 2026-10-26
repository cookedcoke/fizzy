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
DIR=updates
mkdir -p "$DIR" && chmod 777 "$DIR" 2>/dev/null   # Fizzy (another user, in the container) leaves its note here
STATUS="$DIR/update-status.json"
FLAG="$DIR/update-requested"
LOCK="$DIR/.lock"

say() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
json() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\n\r'; }
STATE=""; AT=""; FROM=""; TO=""; MSG=""; BEHIND=0; STEP=""; STARTED=0
[ -f "$STATUS" ] && { STATE=$(sed -n 's/.*"state": *"\([^"]*\)".*/\1/p' "$STATUS"); AT=$(sed -n 's/.*"at": *"\([^"]*\)".*/\1/p' "$STATUS")
                      MSG=$(sed -n 's/.*"message": *"\([^"]*\)".*/\1/p' "$STATUS"); }
write_status() {
    printf '{"state": "%s", "step": "%s", "at": "%s", "from": "%s", "to": "%s", "message": "%s", "behind": %s, "started": %s, "seen": %s}\n' \
        "$(json "$STATE")" "$(json "$STEP")" "$(json "$AT")" "$(json "$FROM")" "$(json "$TO")" "$(json "$MSG")" "${BEHIND:-0}" \
        "${STARTED:-0}" "$(date +%s)" > "$STATUS.tmp" \
        && mv -f "$STATUS.tmp" "$STATUS"
}

fetch() {
    git fetch --quiet 2>/dev/null || { say "Couldn't reach GitHub."; return 1; }
    BEHIND=$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)
}

healthy() {  # Fizzy answers /healthz inside its container
    for _ in $(seq 1 45); do
        docker compose exec -T fizzy python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
            >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

step() { STEP=$1; write_status; }   # what the progress page in Fizzy shows

build() {  # build while the old one keeps serving, then swap (the only time Fizzy is down)
    export FIZZY_VERSION; FIZZY_VERSION="$(git rev-parse --short HEAD)"
    step build; docker compose build fizzy >/dev/null 2>&1 || return 1
    step restart; docker compose up -d fizzy >/dev/null 2>&1
}

update() {
    if ! mkdir "$LOCK" 2>/dev/null; then say "Another update is running."; return 1; fi
    trap 'rmdir "$LOCK" 2>/dev/null' RETURN
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
    step rollback
    git reset --hard --quiet "$old"
    export FIZZY_VERSION; FIZZY_VERSION="$(git rev-parse --short HEAD)"
    docker compose up -d --build fizzy >/dev/null 2>&1 && healthy
    git rev-parse @{u} > "$DIR/bad-version"  # overnight updates skip this one; pressing Update still tries it
    STATE=failed; TO=$FROM; MSG="The new version didn't start, so it went back to $FROM. Check: docker compose logs fizzy"
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
