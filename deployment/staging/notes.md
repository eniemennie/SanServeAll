# Staging Deployment Notes (PythonAnywhere)

1. Create a second PythonAnywhere web app dedicated to staging. No separate
   database to create -- the app uses SQLite (see `env.staging.example`'s
   `SQLITE_DB_PATH` comment), and its file is created automatically on first
   migrate, in its own path so it never collides with production's.
2. Upload/pull the repo, point the WSGI config file at `deployment/staging/wsgi_staging.py`.
3. Copy `deployment/staging/env.staging.example` -> `.env` on the server, fill in real values.
4. `pip install -r backend/requirements/staging.txt`
5. `python manage.py migrate --settings=config.settings.staging`
6. Reload the web app.
7. Same nightly-jobs reliability setup as production (see
   `deployment/production/notes.md` steps 7-8) -- three PythonAnywhere
   Scheduled Tasks calling `run_nightly_forecast`,
   `run_nightly_risk_classification`, and `run_nightly_insight_generation`
   with `--settings=config.settings.staging`, then
   `APSCHEDULER_AUTOSTART=false` once those are confirmed running.
