#!/bin/sh
# Bind-mounted ./data and ./backups are owned by whoever created them on the host (often root, or
# your own user). Fix that, then drop to the unprivileged fizzy user to run the app.
set -e
# Only when something in them isn't fizzy's yet: going through every backup and receipt on every
# start can take minutes on a big backup drive (and the updater waits for Fizzy to come back).
for d in /data /backups; do
    mkdir -p "$d"
    if [ -n "$(find "$d" ! -user fizzy -print -quit 2>/dev/null)" ]; then
        chown -R fizzy:fizzy "$d" 2>/dev/null || echo "warning: couldn't change owner of $d (fine on Windows/macOS)"
    fi
done
exec setpriv --reuid=fizzy --regid=fizzy --init-groups "$@"
