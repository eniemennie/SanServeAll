# Staging Deployment Notes (PythonAnywhere)

1. Create a second PythonAnywhere web app dedicated to staging.
2. Create a separate MySQL database (`sanserveall_staging`).
3. Upload/pull the repo, point the WSGI config file at `deployment/staging/wsgi_staging.py`.
4. Copy `deployment/staging/env.staging.example` -> `.env` on the server, fill in real values.
5. `pip install -r backend/requirements/staging.txt`
6. `python manage.py migrate --settings=config.settings.staging`
7. Reload the web app.
