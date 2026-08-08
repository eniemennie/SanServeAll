# Production Deployment Notes (PythonAnywhere)

Promotion flow: only deploy here via a merged `staging -> main` PR (Phase 5),
verified on staging first, ideally during low-traffic hours (after closing).

1. Create the production PythonAnywhere web app + MySQL database (`sanserveall_production`).
2. Point the WSGI config file at `deployment/production/wsgi_production.py`.
3. Copy `deployment/production/env.production.example` -> `.env`, fill in real values.
4. `pip install -r backend/requirements/production.txt`
5. `python manage.py migrate --settings=config.settings.production`
6. Reload the web app.
7. Run the Week 16 go-live smoke test (login, one test sale per branch type,
   one KaHero batch import, dashboard loads).
