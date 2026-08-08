# SanServeAll — Phase 4: Project Structure

Reflects all Phase 2/3 decisions: Django MTV + DRF, service-layer pattern per app, APScheduler with persistent job store, staging/production environment split, MySQL production / SQLite dev, local-disk media with backup scripts, PythonAnywhere hosting.

No CRUD/feature code is written here — this is folder/file scaffolding and the purpose of each piece only.

---

## 1. Top-Level Layout

```
sanserveall/
│
├── backend/                   # The entire Django project (see §2)
├── docs/                      # All planning docs (Phases 1-10), ERD exports, wireframes
├── database/                  # Raw SQL, seed data, migration snapshots, ERD source files
├── deployment/                # Environment-specific deployment configs/scripts (see §3)
├── .github/                   # GitHub Actions workflows, PR/issue templates (see Phase 5)
├── .gitignore
├── .env.example                # Template for required env vars — never the real .env
└── README.md
```

| Folder | Purpose |
|---|---|
| `backend/` | The single Django project — models, views, templates, static assets, AI module, tests. Everything runtime-related lives here. |
| `docs/` | Living copies of every phase document (this one included), the ERD diagram/export, wireframe images/exports, and the original manuscript reference — keeps planning artifacts versioned alongside code instead of scattered across chat history. |
| `database/` | Non-Django-migration database assets: raw `.sql` seed scripts (e.g., initial branches, roles, product catalog for demo/UAT), ERD source file (e.g., `.dbml`/`.drawio`), and a `backups/` subfolder *pattern* (actual backup files are never committed — see `.gitignore` note in §4). |
| `deployment/` | Everything needed to stand up staging or production that isn't part of the Django codebase itself — PythonAnywhere WSGI config templates, backup cron scripts, environment setup notes. |
| `.github/` | CI workflow definitions (test-on-push) and PR templates — detailed in Phase 5. |
| `.env.example` | Documents every environment variable the app expects (`DB_NAME`, `SECRET_KEY`, `CLAUDE_API_KEY`, etc.) with placeholder values, so a new dev/team member can copy it to `.env` and know exactly what to fill in — the real `.env` is never committed. |
| `README.md` | Project overview, setup instructions, link to `docs/` for full planning documentation. |

---

## 2. `backend/` — Full Django Project, Expanded to Source-Code Level

```
backend/
│
├── manage.py
│
├── config/                              # Django project package (settings, root URLs, WSGI/ASGI, scheduler bootstrap)
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py                      # Shared settings: INSTALLED_APPS, MIDDLEWARE, AUTH_USER_MODEL, REST_FRAMEWORK config, etc.
│   │   ├── development.py               # Imports base.py; SQLite, DEBUG=True, local media, verbose logging
│   │   ├── staging.py                   # Imports base.py; MySQL (staging DB), DEBUG=False, staging .env values
│   │   └── production.py                # Imports base.py; MySQL (production DB), DEBUG=False, production .env values, stricter security headers
│   ├── urls.py                          # Root URL conf — includes each app's urls.py, plus /api/v1/ router
│   ├── wsgi.py                          # WSGI entrypoint (used by PythonAnywhere)
│   ├── asgi.py                          # ASGI entrypoint (kept for future-proofing; not actively used at MVP)
│   └── scheduler.py                     # APScheduler bootstrap: registers jobs (forecast run, risk classification, KaHero ingestion), configures SQLAlchemyJobStore against MySQL, wires failure-alert email hook
│
├── apps/                                 # All Django "apps" (domain modules), each self-contained
│   │
│   ├── core/                             # Shared code with no business domain of its own
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                     # Abstract base models: `TimestampedModel`, `BranchScopedModel` (adds branch FK + queryset scoping)
│   │   ├── permissions.py                # Shared DRF permission classes (e.g., `IsOwnerAdmin`, `IsSameBranch`)
│   │   ├── middleware.py                 # Branch-scoping middleware, PIN-unlock session check
│   │   ├── utils.py                      # Shared helpers (date/time utils, response formatting)
│   │   └── tests/
│   │       └── test_models.py
│   │
│   ├── accounts/                         # Users, Roles, Branches, RBAC, Cashier PIN, 2FA
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                     # `User` (extends AbstractUser), `Role`, `Branch`, `CashierPIN`
│   │   ├── serializers.py                # DRF serializers for user/role/branch endpoints
│   │   ├── services.py                   # Business logic: PIN verification, role-assignment rules, 2FA enrollment flow
│   │   ├── permissions.py                # `IsBranchStaff`, `IsCommissaryStaff`, etc.
│   │   ├── views.py                      # Login, branch-selection, PIN-unlock, admin-login views (template + DRF)
│   │   ├── urls.py
│   │   ├── admin.py                      # Django admin registration for User/Role/Branch
│   │   ├── migrations/
│   │   └── tests/
│   │       ├── test_models.py
│   │       ├── test_services.py
│   │       └── test_views.py
│   │
│   ├── pos/                              # Point of Sale: transactions, line items, receipts
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                     # `SalesTransaction`, `SalesItem`
│   │   ├── serializers.py
│   │   ├── services.py                   # Sale-processing logic: validate order → create transaction → trigger inventory deduction (native POS branches only)
│   │   ├── views.py                      # POS ordering screen, payment processing, receipt view
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │       ├── test_models.py
│   │       └── test_services.py
│   │
│   ├── inventory/                        # Products, stock levels, stock movement, batch materials
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                     # `Product`, `Inventory`, `InventoryTransaction`, `Batch`
│   │   ├── serializers.py
│   │   ├── services.py                   # Stock deduction, reorder-threshold checks, low-stock alert triggering
│   │   ├── views.py                      # Inventory monitoring, product management, batch management screens
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │       ├── test_models.py
│   │       └── test_services.py
│   │
│   ├── production/                       # Commissary production tracking
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                     # `ProductionRecord`, `IngredientUsage`, `SupplyDistribution`
│   │   ├── serializers.py
│   │   ├── services.py                   # Production-to-inventory reconciliation logic
│   │   ├── views.py                      # Commissary data-entry screens, batch processing analytics
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── kahero_integration/               # Alangilan-branch batch-import pipeline (KAHERO_BRANCH config lives here)
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                     # `KaheroImportBatch` (staging/audit table for each uploaded file: filename, uploaded_by, status, row counts, errors)
│   │   ├── parsers.py                    # CSV/Excel parsing + validation logic (pandas-based)
│   │   ├── services.py                   # Orchestrates: validate file → stage rows → ingest into pos/inventory models → mark batch complete/failed
│   │   ├── views.py                      # File upload endpoint/screen, import-status view
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │       ├── test_parsers.py
│   │       └── test_services.py
│   │
│   ├── analytics/                        # Dashboard read-side: consolidated reporting data
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                     # `AnalyticsSnapshot` (consolidated periodic rollups feeding dashboards)
│   │   ├── serializers.py
│   │   ├── services.py                   # Aggregation queries (sales trends, resource consumption, operational performance)
│   │   ├── views.py                      # Analytics Dashboard, Sales Analytics, Product Performance, Resource Consumption, Operational Performance screens
│   │   ├── urls.py
│   │   └── tests/
│   │
│   └── forecasting/                      # The AI/DSS module
│       ├── __init__.py
│       ├── apps.py
│       ├── models.py                     # `Forecast`, `InventoryRiskScore`
│       ├── serializers.py
│       ├── services.py                   # Orchestration: pulls data via accounts/pos/inventory, calls ml/ layer, stores results
│       ├── ml/
│       │   ├── __init__.py
│       │   ├── data_prep.py              # pandas/numpy cleaning, aggregation of historical sales for model input
│       │   ├── arima_model.py            # statsmodels ARIMA fit/predict wrapper
│       │   ├── risk_classifier.py        # scikit-learn inventory risk classification wrapper
│       │   └── insight_generator.py      # Claude API / OpenAI API call — turns numeric outputs into natural-language recommendations
│       ├── views.py                      # AI-Powered Decision Support Interface, Forecasting Dashboard, Resource Management Dashboard
│       ├── urls.py
│       ├── jobs.py                       # Scheduled-job entrypoints registered in config/scheduler.py (forecast_job(), risk_classification_job())
│       ├── migrations/
│       └── tests/
│           ├── test_arima_model.py       # Accuracy/error-metric checks (MAE/RMSE against holdout data)
│           ├── test_risk_classifier.py
│           └── test_services.py
│
├── templates/                            # Django templates, shared across apps
│   ├── base.html                         # Shared layout shell (nav, footer, Bootstrap 5 + Chart.js includes)
│   ├── accounts/                         # login.html, branch_selection.html, pin_auth.html, admin_login.html
│   ├── pos/                              # pos_ordering.html, add_custom_product.html, order_customization.html, payment.html, receipt.html
│   ├── inventory/                        # inventory_monitoring.html, product_management.html, batch_management.html
│   ├── production/                       # production_entry.html, batch_processing_dashboard.html
│   ├── analytics/                        # analytics_dashboard.html, sales_analytics.html, product_performance.html, resource_consumption.html, operational_performance.html
│   ├── forecasting/                      # ai_decision_support.html, forecasting_dashboard.html, resource_management_dashboard.html
│   └── settings/                         # system_settings.html, system_configuration.html
│
├── static/                                # Source static assets (pre-collectstatic)
│   ├── css/
│   │   ├── base.css
│   │   ├── pos.css
│   │   └── dashboard.css
│   ├── js/
│   │   ├── pos.js                        # POS ordering/payment AJAX logic
│   │   ├── dashboard.js                  # Chart.js rendering, dashboard fetch calls
│   │   └── branch_filter.js              # Branch Filter Dropdown behavior
│   └── img/                              # Logo, icons
│
├── media/                                 # Uploaded files at runtime (KaHero exports, generated receipt/report PDFs) — gitignored, see §4
│
├── requirements/
│   ├── base.txt                          # Django, djangorestframework, mysqlclient, pandas, numpy, statsmodels, scikit-learn, apscheduler, django-otp, etc.
│   ├── development.txt                   # base.txt + debugging tools (django-debug-toolbar), pytest-django
│   ├── staging.txt                       # base.txt + WhiteNoise, staging-specific packages
│   └── production.txt                    # base.txt + WhiteNoise, gunicorn (if applicable), production-only hardening packages
│
├── scripts/
│   ├── backup_media_and_db.sh            # Nightly backup script (media/ folder + mysqldump) referenced by Phase 3 durability decision
│   ├── seed_demo_data.py                 # Loads database/ seed SQL for local dev/demo
│   └── run_scheduler_check.py            # Manual health-check script to confirm APScheduler jobs are registered/firing
│
├── pytest.ini                             # pytest-django configuration
├── conftest.py                            # Shared pytest fixtures (test users, test branches, etc.)
└── logs/                                  # Local dev log output (gitignored) — staging/production logging destination configured separately in deployment/
```

**Why `apps/` is a sub-package instead of Django apps sitting at `backend/` root:** keeps `config/` (project-level settings/URLs) visually and structurally separate from the domain apps, so nobody confuses "the Django project" with "a Django app" — a common source of import confusion in flatter layouts.

**Why every domain app has both `views.py` and `services.py`:** enforces the service-layer pattern from Phase 2 — `views.py` stays thin (HTTP request/response handling only), `services.py` holds the actual business rules (e.g., "when a sale is recorded, deduct inventory and check reorder thresholds"). This makes business logic unit-testable without spinning up HTTP requests, and keeps `forecasting/ml/` cleanly separated from the orchestration in `forecasting/services.py`.

---

## 3. `deployment/` — Expanded

```
deployment/
├── staging/
│   ├── wsgi_staging.py                   # PythonAnywhere WSGI config pointing at config.settings.staging
│   ├── env.staging.example               # Documents staging-specific env vars (staging DB name, staging API keys)
│   └── notes.md                          # Step-by-step: how staging is set up on PythonAnywhere
├── production/
│   ├── wsgi_production.py                # PythonAnywhere WSGI config pointing at config.settings.production
│   ├── env.production.example
│   └── notes.md                          # Step-by-step: how production is set up, promotion checklist (see Phase 3 staging→production flow)
└── backup/
    └── cron_backup_setup.md              # Documents the scheduled task on PythonAnywhere that runs scripts/backup_media_and_db.sh nightly
```

---

## 4. `.gitignore` — Key Entries and Why

| Pattern | Why ignored |
|---|---|
| `*.env`, `.env.*` (except `.example` files) | Never commit real secrets (DB passwords, `SECRET_KEY`, Claude/OpenAI API keys). |
| `backend/media/` | Runtime-uploaded files (KaHero exports, generated receipts) — not source, and can contain real business/customer data. |
| `backend/logs/` | Local log output, not meant for version control. |
| `db.sqlite3` | Local dev database — regenerated per developer, never shared via Git. |
| `__pycache__/`, `*.pyc` | Standard Python bytecode. |
| `database/backups/*` (folder tracked via `.gitkeep`, contents ignored) | Backup dumps live here locally/temporarily but are never committed — they belong in the actual backup destination (§3), not Git history. |
| `.venv/`, `venv/` | Local virtual environment, rebuilt from `requirements/`. |
| `staticfiles/` (collectstatic output) | Generated, not source — regenerated on deploy. |

---

## 5. How This Structure Reflects Prior Phase Decisions

| Phase 2/3 decision | Where it shows up here |
|---|---|
| Service-layer pattern on top of MVT | `services.py` in every domain app |
| APScheduler + persistent job store | `config/scheduler.py` + `apps/forecasting/jobs.py` |
| KAHERO_BRANCH as a config value, not hardcoded logic | Isolated entirely inside `apps/kahero_integration/`, referenced by branch config in `accounts` — not scattered across `pos`/`inventory` |
| Staging + Production split | `config/settings/staging.py` + `production.py`, mirrored `deployment/staging/` + `deployment/production/` |
| Local disk + automated backups | `media/` (gitignored) + `scripts/backup_media_and_db.sh` + `deployment/backup/` |
| MySQL prod / SQLite dev | `config/settings/development.py` vs. `staging.py`/`production.py` |
| DRF API layer, versioned | `config/urls.py` routes to `/api/v1/` per app |
| AI module decoupled from live POS | `apps/forecasting/ml/` (pure computation) is only ever called from `apps/forecasting/jobs.py` (scheduled), never from `apps/pos/services.py` directly |

---

Ready for **Phase 5 — GitHub Strategy** whenever you'd like to continue.
