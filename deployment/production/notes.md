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

Promotion flow, as actually practiced: the live app's working directory
tracks `staging` directly (confirmed above) and is updated with `git pull`
on that branch after each PR merges into `staging` -- there is no separate
`main`-tracking deployment target, so the Phase 5 "`staging -> main` then
deploy from `main`" flow was never actually followed in practice. `main`
still exists in the repo, but as of this writing it is 84 commits behind
`staging` (last synced 2026-11-02, PR #136) and is not being kept current
as a release-tagging record either (see Phase 5 Versioning) -- nothing
currently deploys from it, and nothing is currently promoting into it.
Treat any future reference to "promote to main, then deploy" as describing
the aspirational flow, not what's on the live site; if the team wants
`main` to mean something again before the defense, it needs a deliberate
`staging -> main` catch-up PR, not an assumption that it's already
tracking.

1. Create the production PythonAnywhere web app. No separate database to create --
   the app uses SQLite (see `env.production.example`'s `SQLITE_DB_PATH` comment),
   and its file is created automatically on first migrate.
2. Point the WSGI config file at `deployment/production/wsgi_production.py`.
3. Copy `deployment/production/env.production.example` -> `.env`, fill in real values.
4. `pip install -r backend/requirements/production.txt`
5. `python manage.py migrate --settings=config.settings.production`
6. Reload the web app.
7. Set up the three nightly forecasting jobs as **PythonAnywhere Scheduled
   Tasks**, not just the in-process scheduler that starts automatically with
   the web app. The web app's own APScheduler (`config/scheduler.py`) only
   runs while that specific worker process is alive, which PythonAnywhere's
   free tier does not guarantee at a fixed time of night -- idle workers can
   restart on the next incoming request. A real, independent Scheduled Task
   is the reliable path. Under the "Tasks" tab, add one task per command
   below. **Times are UTC** (PythonAnywhere always schedules in UTC,
   regardless of the app's `TIME_ZONE` setting of `Asia/Manila`, UTC+8) --
   the times below are already converted from the jobs' local 2:00/2:30/3:00
   AM:

   | Command | UTC time (= local time) |
   | --- | --- |
   | `python /home/<username>/<project-path>/backend/manage.py run_nightly_forecast --settings=config.settings.production` | 18:00 (= 2:00 AM Asia/Manila) |
   | `python /home/<username>/<project-path>/backend/manage.py run_nightly_risk_classification --settings=config.settings.production` | 18:30 (= 2:30 AM Asia/Manila) |
   | `python /home/<username>/<project-path>/backend/manage.py run_nightly_insight_generation --settings=config.settings.production` | 19:00 (= 3:00 AM Asia/Manila) |

   Use the full path to the virtualenv's `python` (shown on the Tasks tab
   itself once the task is saved) so the command runs with the right
   dependencies, not the system Python.
8. Once all three Scheduled Tasks are confirmed running (check their logs
   after the first overnight run), set `APSCHEDULER_AUTOSTART=false` in the
   production `.env` and reload the web app, so the in-process scheduler and
   the Scheduled Tasks don't both run the same job. Leaving it unset/`true`
   is safe in the meantime -- the in-process scheduler still runs today, it's
   just the unreliable path being replaced.
9. Run the Week 16 go-live smoke test (login, one test sale per branch type,
   one KaHero batch import, dashboard loads).
