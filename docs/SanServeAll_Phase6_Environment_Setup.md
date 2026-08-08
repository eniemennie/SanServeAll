# SanServeAll — Phase 6: Environment Setup

The original Phase 6 checklist template includes items (Firebase CLI, Flutter SDK, Java, Android Studio) that don't apply here — SanServeAll is a Django + MySQL + server-rendered web app with no Firebase backend and no native mobile app in scope (explicitly excluded in Phase 1). Those are marked **N/A** below rather than installed just to check a box, and the list is built around what the confirmed stack (Phases 2–3) actually requires.

---

## 1. Required for Every Team Member

| Tool | Version | Purpose |
|---|---|---|
| **Python** | 3.11+ | Runtime for Django, DRF, and all AI libraries (statsmodels, pandas, numpy, scikit-learn). |
| **pip** | Latest (bundled with Python) | Installs dependencies from `requirements/development.txt`. |
| **venv** (or `virtualenv`) | Bundled with Python 3.11+ | Isolates project dependencies per machine — never install packages globally. |
| **Git** | 2.40+ | Version control (Phase 5 strategy). |
| **MySQL Server** | 8.x | Needed locally **only if** you want to test against MySQL before pushing to staging (SQLite is the default dev DB per Phase 3 — this is optional but recommended before any PR that changes models/migrations, since SQLite and MySQL don't always behave identically). |
| **A MySQL client/GUI** | e.g., MySQL Workbench, DBeaver, or TablePlus | Inspecting staging/production data structure, running manual queries during debugging. |
| **A modern browser** | Latest Chrome or Firefox (either is fine; pick one as the team's shared "reference browser" for consistent testing) | Testing the POS/dashboard UI, using browser DevTools for JS/network debugging. |
| **Postman** (or Thunder Client, see VS Code extensions below) | Latest | Manually testing DRF API endpoints (`/api/v1/...`) independent of the frontend — critical while POS/dashboard AJAX calls are being built against APIs that may not have UI yet. |

## 2. Code Editor: VS Code (recommended) + Extensions

| Extension | Why |
|---|---|
| **Python** (Microsoft) | Core Python language support, linting, debugging. |
| **Pylance** | Faster/better type-checking and autocomplete for Python. |
| **Django** (by Baptiste Darthenay or similar) | Django template/tag syntax highlighting, `{% %}` and `{{ }}` support. |
| **MySQL** (by Jun Han, or use the standalone GUI client instead) | Optional — quick DB browsing without leaving the editor. |
| **GitLens** | Inline blame/history — useful for a 3-person team reviewing each other's changes. |
| **Thunder Client** | In-editor alternative to Postman for quick API testing without switching apps. |
| **EditorConfig for VS Code** | Enforces the `.editorconfig` file (see Phase 7 coding standards) across everyone's editor automatically. |
| **Prettier** | Optional — consistent formatting for the vanilla JS/CSS files (not for Python; use `black`/`isort` instead, see below). |

## 3. Python Tooling (installed via `requirements/development.txt`, not manually)

| Tool | Purpose |
|---|---|
| `django` (5.x) | Web framework |
| `djangorestframework` | API layer |
| `mysqlclient` | MySQL DB adapter (used once developer switches settings to test against local MySQL, and always in staging/production) |
| `pandas`, `numpy` | AI data preprocessing |
| `statsmodels` | ARIMA forecasting |
| `scikit-learn` | Inventory risk classification |
| `apscheduler` | Background job scheduling (with `SQLAlchemyJobStore` support) |
| `django-otp` | 2FA for Owner/Admin accounts |
| `pytest`, `pytest-django` | Testing (Phase 3) |
| `django-debug-toolbar` | Dev-only — SQL query inspection, request profiling |
| `black`, `isort`, `flake8` | Python formatting/linting — enforced as coding standards in Phase 7 |
| `python-dotenv` | Loads `.env` values into Django settings |

*(This table exists here so the team knows what `pip install -r requirements/development.txt` actually brings in — no manual installation of these is needed beyond running that command.)*

## 4. Design Collaboration

| Tool | Purpose |
|---|---|
| **Figma** | Reviewing/refining the 28 UI screens from Phase 1 before building templates — even though the manuscript's wireframes already exist as figures, Figma is useful for the team to iterate on any UI adjustments collaboratively before writing HTML/CSS. |

## 5. Deployment/Hosting Access

| Item | Purpose |
|---|---|
| **PythonAnywhere account** (shared or per-member, per your team's preference) | Staging and production hosting (Phase 3). At least one team member needs owner/admin access to both the staging and production web app consoles. |
| **GitHub repository access** | All 3 members added as collaborators with appropriate branch permissions (Phase 5). |
| **Claude API key / OpenAI API key** | Required for the natural-language insight generation feature (Phase 1 FR-04) — obtain a developer key from Anthropic Console or OpenAI Platform, store only in `.env` (never committed, per Phase 5 `.gitignore`). |

## 6. Explicitly Not Needed (N/A for this project)

| Tool (from generic template) | Why it doesn't apply |
|---|---|
| **Firebase CLI** | Not using Firebase — auth, hosting, and DB are all Django/MySQL/PythonAnywhere per Phase 2–3. |
| **Flutter SDK** | No mobile app in scope (Phase 1 explicitly excludes native mobile support). |
| **Java / Android Studio** | Same reason — no native Android build exists in this project. |
| **Node.js** | Not strictly required — the frontend uses vanilla JS + Bootstrap 5 + Chart.js loaded directly (no npm build pipeline, per Phase 3). *Optional* if you later want Prettier/ESLint run via npm scripts instead of the VS Code extension alone — not required to develop or run the app. |

---

## 7. Local Setup Order (once tools above are installed)

1. Clone the repo, create and activate a `venv`.
2. `pip install -r backend/requirements/development.txt`
3. Copy `.env.example` → `.env`, fill in local values (SQLite needs no DB credentials; Claude/OpenAI keys still required to exercise the AI insight feature locally).
4. `python manage.py migrate` (applies migrations against local SQLite)
5. `python scripts/seed_demo_data.py` (loads sample branches/roles/products from `database/` for local testing)
6. `python manage.py runserver`
7. (Optional) Point local settings at a local MySQL instance before opening a PR that touches models, to catch any MySQL-specific migration issues early.

---

Ready for **Phase 7 — Development Standards** whenever you'd like to continue.
