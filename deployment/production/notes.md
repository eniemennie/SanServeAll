# Production Deployment Notes (PythonAnywhere)

**Current status (as of November 2026): this IS what's deployed.**
PythonAnywhere's free tier allows exactly one web app per account, and
the team's one app (`sanserveall.pythonanywhere.com`) runs
`config.settings.production` against `sanserveall_production.sqlite3`
-- confirmed directly from the live WSGI file and `.env` on 2026-11-03.
It is the one real, live system used for verification, demos, staff
logins, and adviser/client review, carrying real production/sales/
transfer history (see the Nov 3 2026 deployment-gap/500-error incident
notes in the project's task tracker for how this was confirmed).

Earlier versions of this file described this as "not yet deployed,"
reasoning that the team's one app was running staging settings instead
-- that was never actually true in practice: the live app's WSGI config
and `.env` have set `DJANGO_SETTINGS_MODULE=config.settings.production`
for as long as can be confirmed, independent of whatever `staging`
branch state existed at the time. `deployment/staging/notes.md` now
describes a genuinely unused, aspirational second-app setup (not
currently needed), rather than this live app.

Standing up a real second PythonAnywhere app for actual staging testing
would require a paid plan (e.g. the "Hacker" tier), which the team has
decided isn't worth it for a capstone project.

Which git branch this app's working directory tracks (confirmed
`staging` as of the Nov 3 2026 deploy-catch-up, commit `10b32b2`) is a
separate question from which Django settings module it runs
(`production`, confirmed above) -- don't assume these always point the
same direction without checking both directly.

Promotion flow: only deploy here via a merged `staging -> main` PR (Phase 5),
verified on staging first, ideally during low-traffic hours (after closing).

1. Create the production PythonAnywhere web app. No separate database to create --
   the app uses SQLite (see `env.production.example`'s `SQLITE_DB_PATH` comment),
   and its file is created automatically on first migrate.
2. Point the WSGI config file at `deployment/production/wsgi_production.py`.
3. Copy `deployment/production/env.production.example` -> `.env`, fill in real values.
4. `pip install -r backend/requirements/production.txt`
5. `python manage.py migrate --settings=config.settings.production`
6. Reload the web app.
7. Set up all **four** scheduled jobs registered in `config/scheduler.py` as
   **PythonAnywhere Scheduled Tasks**, not just the in-process scheduler that
   starts automatically with the web app. The web app's own APScheduler only
   runs while that specific worker process is alive, which PythonAnywhere's
   free tier does not guarantee at a fixed time of day -- idle workers can
   restart on the next incoming request. A real, independent Scheduled Task
   is the reliable path. Under the "Tasks" tab, add one task per command
   below. **Times are UTC** (PythonAnywhere always schedules in UTC,
   regardless of the app's `TIME_ZONE` setting of `Asia/Manila`, UTC+8) --
   the times below are already converted from the jobs' local 2:00/2:30/3:00/
   6:00 AM:

   | Command | UTC time (= local time) |
   | --- | --- |
   | `python /home/<username>/<project-path>/backend/manage.py run_nightly_forecast --settings=config.settings.production` | 18:00 (= 2:00 AM Asia/Manila) |
   | `python /home/<username>/<project-path>/backend/manage.py run_nightly_risk_classification --settings=config.settings.production` | 18:30 (= 2:30 AM Asia/Manila) |
   | `python /home/<username>/<project-path>/backend/manage.py run_nightly_insight_generation --settings=config.settings.production` | 19:00 (= 3:00 AM Asia/Manila) |
   | `python /home/<username>/<project-path>/backend/manage.py run_low_stock_alert --settings=config.settings.production` | 22:00 (= 6:00 AM Asia/Manila) |

   The fourth row (`run_low_stock_alert`) was added 2026-11-07: the job
   itself (`daily_low_stock_alert`, 6 AM local) has been registered in
   `config/scheduler.py` since the low-stock alerting feature shipped, but
   unlike the other three jobs it had no standalone management command of
   its own until now -- so there was previously no way to put it on a real
   Scheduled Task at all. That mattered here specifically: step 8 below
   turns off the in-process scheduler once the Scheduled Tasks are confirmed
   working, and with the command missing, that would have silently disabled
   low-stock alerting entirely rather than migrating it.

   Use the full path to the virtualenv's `python` (shown on the Tasks tab
   itself once the task is saved) so the command runs with the right
   dependencies, not the system Python.
8. Once all four Scheduled Tasks are confirmed running (check their logs
   after the first overnight run), set `APSCHEDULER_AUTOSTART=false` in the
   production `.env` and reload the web app, so the in-process scheduler and
   the Scheduled Tasks don't both run the same job. Leaving it unset/`true`
   is safe in the meantime -- the in-process scheduler still runs today, it's
   just the unreliable path being replaced.
9. Run the Week 16 go-live smoke test (login, one test sale per branch type,
   one KaHero batch import, dashboard loads).
