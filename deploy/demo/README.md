# Demo server

A Fizzy with made-up students and sales, for App Store screenshots and for App Review to sign in
to. It runs on the homelab as container 113 (`site-fizzy-demo`), public at
https://fizzy-demo.w-l.xyz (staff app) and https://fizzy-demo.w-l.xyz/me (My CCC).

- `seed_demo.py`: fills an empty database: 44 students, about two weeks of sales, tabs, a reward,
  a weekly double points day, restocks and payouts, and student passcodes. Amelia Williams' is
  2468; the staff passcode is `DEMO_PIN` (4821 by default). Nothing on the dashboard warns: Square
  looks synced and a backup has just run.
- `demo_app.py`: the app without the background jobs, so it never tries to reach Square.
- `settings.json`: the permissions the demo uses.
- `setup.sh`: run once inside the container after installing Fizzy to /opt/fizzy/app. Makes the
  service, reseeds every morning at 05:30 (so the reviewer always sees sales "today"), and adds
  `fizzy-demo-set-password` for the staff password, which the owner types themselves.

Apple Pay (so App Review can find My CCC's Pay with Apple Pay button on Amelia's tab, which the seed
always leaves owing $5.50) uses Square's sandbox. Add these to `/etc/fizzy-demo/env` in place of the
`demo-not-real` token, from the Sandbox tab of the Square Developer dashboard, then restart `fizzy-demo`:
`FIZZY_APPLE_PAY=1`, `SQUARE_ENVIRONMENT=sandbox`, `SQUARE_ACCESS_TOKEN`, `SQUARE_APPLICATION_ID`, `SQUARE_LOCATION_ID`.

To try it locally:

```bash
FIZZY_DB=/tmp/demo.db FIZZY_BACKUP_DIR=/tmp/demo-backups FIZZY_PASSWORD=... FIZZY_SECRET_KEY=... \
  python deploy/demo/seed_demo.py deploy/demo/settings.json
SQUARE_ACCESS_TOKEN=demo-not-real PYTHONPATH=deploy/demo:. FIZZY_DB=/tmp/demo.db ... uvicorn demo_app:app
```
