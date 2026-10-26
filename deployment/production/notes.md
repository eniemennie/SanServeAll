# Production Deployment Notes (PythonAnywhere)

Promotion flow: only deploy here via a merged `staging -> main` PR (Phase 5),
verified on staging first, ideally during low-traffic hours (after closing).

1. Create the production PythonAnywhere web app + MySQL database (`sanserveall_production`).
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
