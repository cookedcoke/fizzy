#!/bin/sh
# Bind-mounted ./data and ./backups are owned by whoever created them on the host (often root, or
# your own user). Fix that, then drop to the unprivileged fizzy user to run the app.
set -e
for d in /data /backups; do
    mkdir -p "$d"
    chown -R fizzy:fizzy "$d" 2>/dev/null || echo "warning: couldn't change owner of $d (fine on Windows/macOS)"
done
exec setpriv --reuid=fizzy --regid=fizzy --init-groups "$@"
