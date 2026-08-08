# SanServeAll — Phase 8: Configuration Files

Every configuration file that will exist in the repository, grouped by purpose, reflecting all decisions from Phases 2–7 (staging/production split, MySQL/SQLite, APScheduler persistent store, PythonAnywhere hosting, testing stack, coding standards).

---

## 1. Environment & Secrets

| File | Location | Purpose |
|---|---|---|
| `.env.example` | repo root | Documents every environment variable the app expects, with placeholder values — `SECRET_KEY`, `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `CLAUDE_API_KEY` / `OPENAI_API_KEY`, `DEBUG`, `ALLOWED_HOSTS`. Committed to Git; the real `.env` never is. |
| `.env` | repo root (gitignored) | Actual local development secrets/config, loaded via `python-dotenv`. Never committed (Phase 5 `.gitignore`). |
| `deployment/staging/env.staging.example` | `deployment/staging/` | Documents staging-specific values (staging DB credentials, staging API keys) — real `.env` for staging lives only on the staging PythonAnywhere instance. |
| `deployment/production/env.production.example` | `deployment/production/` | Same, for production — real values live only on the production PythonAnywhere instance. |

---

## 2. Django Settings

| File | Location | Purpose |
|---|---|---|
| `config/settings/base.py` | `backend/config/settings/` | Shared settings across all environments: `INSTALLED_APPS`, `MIDDLEWARE`, `AUTH_USER_MODEL`, `REST_FRAMEWORK` config, `TEMPLATES`, static/media path definitions, `LOGGING` base config. |
| `config/settings/development.py` | same | Overrides for local dev: SQLite `DATABASES`, `DEBUG=True`, `django-debug-toolbar` enabled, local `media/` path. |
| `config/settings/staging.py` | same | Overrides for staging: MySQL `DATABASES` (staging DB), `DEBUG=False`, staging `ALLOWED_HOSTS`, staging logging destination. |
| `config/settings/production.py` | same | Overrides for production: MySQL `DATABASES` (production DB), `DEBUG=False`, production `ALLOWED_HOSTS`, stricter security headers (`SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`), production logging destination. |
| `config/urls.py` | `backend/config/` | Root URL configuration — not a "config file" in the settings sense, but functions as routing configuration: includes each app's `urls.py`, mounts `/api/v1/`, mounts `drf-spectacular`'s schema/Swagger UI (dev only). |
| `config/scheduler.py` | `backend/config/` | APScheduler bootstrap/configuration: registers `SQLAlchemyJobStore` pointed at MySQL, registers jobs from `apps/forecasting/jobs.py` and `apps/kahero_integration/` ingestion job, configures the failure-alert email hook (Phase 3 revision). |

---

## 3. Python Dependency Management

| File | Location | Purpose |
|---|---|---|
| `requirements/base.txt` | `backend/requirements/` | Core dependencies needed in every environment (Django, DRF, mysqlclient, pandas, numpy, statsmodels, scikit-learn, apscheduler, django-otp, drf-spectacular). |
| `requirements/development.txt` | same | `-r base.txt` + dev-only tools: `django-debug-toolbar`, `pytest`, `pytest-django`, `black`, `isort`, `flake8`. |
| `requirements/staging.txt` | same | `-r base.txt` + `whitenoise` + any staging-only diagnostic packages. |
| `requirements/production.txt` | same | `-r base.txt` + `whitenoise` + production-hardening packages (if any added later, e.g. `sentry-sdk` if error monitoring is adopted). |

---

## 4. Code Quality / Formatting

| File | Location | Purpose |
|---|---|---|
| `pyproject.toml` | repo root | Central config for `black` (line length, target Python version) and `isort` (import ordering, compatible profile with `black`) — single source of truth instead of scattering settings across multiple tool-specific files. |
| `setup.cfg` (or `.flake8`) | repo root | `flake8` configuration — max line length matching `black`, ignored rules where `black`/`flake8` intentionally disagree, excluded paths (`migrations/`, `venv/`). |
| `.editorconfig` | repo root | Cross-editor consistency (indent size/style, line endings, trailing whitespace) — enforced automatically in VS Code via the EditorConfig extension (Phase 6). |

---

## 5. Testing

| File | Location | Purpose |
|---|---|---|
| `pytest.ini` | `backend/` | `pytest-django` configuration: points at `config.settings.development` (or a dedicated `config.settings.test`, recommended — see note below), test discovery patterns. |
| `conftest.py` | `backend/` | Shared pytest fixtures: test users per role, test branches (including a fixture explicitly representing the `KAHERO_BRANCH` config), sample products/inventory for service-layer tests. |

**Note worth flagging:** rather than running tests directly against `development.py`, it's cleaner to add a `config/settings/test.py` (SQLite, migrations disabled/fast, `APSCHEDULER` jobs not auto-started) so test runs never accidentally touch a real dev database or trigger scheduled jobs. Minor addition, easy to fold in whenever Phase 8 gets implemented for real.

---

## 6. CI/CD

| File | Location | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | `.github/workflows/` | GitHub Actions workflow: on every PR to `staging` or `main`, installs `requirements/development.txt`, runs `flake8`/`black --check`, runs `pytest`. Required status check for branch protection (Phase 5). |
| `.github/pull_request_template.md` | `.github/` | Already defined in Phase 5 — the PR description template. |
| `.github/CODEOWNERS` (optional) | `.github/` | Optional for a 3-person team — could designate one member as required reviewer for sensitive areas (e.g., `apps/forecasting/` or `config/settings/production.py`) if the team wants that extra safeguard. |

---

## 7. Deployment

| File | Location | Purpose |
|---|---|---|
| `deployment/staging/wsgi_staging.py` | `deployment/staging/` | WSGI entrypoint for the staging PythonAnywhere web app, pointed at `config.settings.staging`. |
| `deployment/production/wsgi_production.py` | `deployment/production/` | WSGI entrypoint for the production PythonAnywhere web app, pointed at `config.settings.production`. |
| `deployment/backup/cron_backup_setup.md` | `deployment/backup/` | Not an executable config file, but documents the PythonAnywhere scheduled-task configuration that invokes `scripts/backup_media_and_db.sh` nightly (Phase 3 durability decision). |

---

## 8. Database

| File | Location | Purpose |
|---|---|---|
| `database/seed_data.sql` | `database/` | Initial data for demo/UAT: sample branches (Batangas City, Alangilan, Lipa City), roles, a starter product catalog. |
| Django migration files (`apps/*/migrations/000X_*.py`) | per app | Auto-generated schema-change history — not hand-written config, but functions as the versioned "database configuration" of the project. |

---

## 9. Documentation-as-Configuration

| File | Location | Purpose |
|---|---|---|
| `README.md` | repo root | Project overview, quickstart, links to `docs/`. |
| `docs/` phase files (Phases 1–8, this one included) | `docs/` | Copies of every planning document, kept versioned alongside code rather than living only in chat history. |

---

## Summary Checklist (files to actually create when scaffolding begins)

- [ ] `.env.example`, `deployment/staging/env.staging.example`, `deployment/production/env.production.example`
- [ ] `config/settings/{base,development,staging,production,test}.py`
- [ ] `config/scheduler.py`
- [ ] `requirements/{base,development,staging,production}.txt`
- [ ] `pyproject.toml`, `setup.cfg` (or `.flake8`), `.editorconfig`
- [ ] `pytest.ini`, `conftest.py`
- [ ] `.github/workflows/ci.yml`, `.github/pull_request_template.md`, (optional) `.github/CODEOWNERS`
- [ ] `deployment/staging/wsgi_staging.py`, `deployment/production/wsgi_production.py`
- [ ] `deployment/backup/cron_backup_setup.md`
- [ ] `database/seed_data.sql`
- [ ] `README.md`

---

Ready for **Phase 9 — Development Timeline** whenever you'd like to continue.
