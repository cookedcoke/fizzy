set -e
mkdir -p /etc/fizzy-demo
# Settings. The secret key is made here and never leaves the box.
cat > /etc/fizzy-demo/env <<EOF
FIZZY_DB=/var/lib/fizzy/fizzy.db
FIZZY_BACKUP_DIR=/var/lib/fizzy/backups
FIZZY_BACKUP_KEEP=3
FIZZY_SECURE_COOKIES=1
SQUARE_ACCESS_TOKEN=demo-not-real
FIZZY_SRC=/opt/fizzy/app
PYTHONPATH=/opt/fizzy:/opt/fizzy/app
FIZZY_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
EOF
touch /etc/fizzy-demo/password
chown root:fizzy /etc/fizzy-demo/env /etc/fizzy-demo/password
chmod 640 /etc/fizzy-demo/env /etc/fizzy-demo/password

# Fresh made-up data: run daily so the reviewer always sees sales "today".
cat > /opt/fizzy/reseed.sh <<'EOF'
#!/bin/sh
set -e
rm -f /var/lib/fizzy/fizzy.db /var/lib/fizzy/fizzy.db-wal /var/lib/fizzy/fizzy.db-shm
cd /opt/fizzy && /opt/fizzy/venv/bin/python seed_demo.py settings.json
EOF
chmod 755 /opt/fizzy/reseed.sh

cat > /etc/systemd/system/fizzy-demo.service <<'EOF'
[Unit]
Description=Fizzy demo server (made-up data, for App Store review)
After=network-online.target

[Service]
User=fizzy
WorkingDirectory=/opt/fizzy
EnvironmentFile=/etc/fizzy-demo/env
EnvironmentFile=/etc/fizzy-demo/password
ExecStart=/opt/fizzy/venv/bin/uvicorn demo_app:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/fizzy-demo-reseed.service <<'EOF'
[Unit]
Description=Reseed the Fizzy demo with fresh made-up data

[Service]
Type=oneshot
EnvironmentFile=/etc/fizzy-demo/env
ExecStartPre=-/bin/systemctl stop fizzy-demo
ExecStart=/sbin/runuser -u fizzy -- /opt/fizzy/reseed.sh
ExecStartPost=/bin/systemctl start fizzy-demo
EOF

cat > /etc/systemd/system/fizzy-demo-reseed.timer <<'EOF'
[Unit]
Description=Fresh Fizzy demo data every morning

[Timer]
OnCalendar=*-*-* 05:30
Persistent=true

[Install]
WantedBy=timers.target
EOF

# The owner sets the staff password with a hidden prompt.
cat > /usr/local/sbin/fizzy-demo-set-password <<'EOF'
#!/usr/bin/env python3
"""Set the demo's staff password (the one App Review signs in with)."""
import getpass, os, subprocess
pw = getpass.getpass("Demo staff password: ")
if not pw or pw != getpass.getpass("Again: "):
    raise SystemExit("Didn't match, nothing changed.")
if "\n" in pw:
    raise SystemExit("No line breaks, please.")
path = "/etc/fizzy-demo/password"
with open(path, "w") as f:
    f.write("FIZZY_PASSWORD=" + pw + "\n")
os.chmod(path, 0o640)
subprocess.run(["systemctl", "restart", "fizzy-demo"], check=True)
print("Saved, and the demo restarted.")
EOF
chmod 700 /usr/local/sbin/fizzy-demo-set-password

systemctl daemon-reload
systemctl enable -q fizzy-demo fizzy-demo-reseed.timer
systemctl start fizzy-demo-reseed.timer
runuser -u fizzy -- env $(grep -v SECRET /etc/fizzy-demo/env | xargs) /opt/fizzy/reseed.sh
echo setup done
