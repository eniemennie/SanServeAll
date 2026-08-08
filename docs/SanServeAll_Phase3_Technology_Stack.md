# SanServeAll — Phase 3: Technology Stack

**Revision note:** This system is confirmed as a **mandatory real production deployment** for Jorge's Casa De Sans Rival, not just a capstone demo. This changes three of the original defaults (background job scheduler, file storage, environment strategy) toward durability and safety over minimum setup — those sections are marked ✅ **Revised** below. Everything else from the original pass still holds since it wasn't affected by this constraint.

---

## 1. Frontend

| Layer | Choice | Why |
|---|---|---|
| **Framework** | Django Templates (server-rendered) + vanilla JavaScript for interactivity | Matches the manuscript's own stack (Table 3-3: HTML5/CSS3/JS/Bootstrap 5, Django MTV). A SPA framework (React/Vue) isn't in the defended paper and would add a build pipeline, API-auth complexity (CORS, tokens), and a second codebase to maintain — none of which the capstone timeline or panel expects. |
| **Language** | JavaScript (ES6+), no TypeScript | Keeps the frontend lightweight and matches vanilla-JS scope in the manuscript. TypeScript is a nice-to-have but not justified for a template-driven app with a handful of AJAX endpoints. |
| **UI Library** | Bootstrap 5 | Explicitly named in the manuscript (Table 3-3). Gives responsive, mobile-first layout out of the box — important since branch staff and owner may use different screen sizes (tablet POS vs. desktop dashboard). |
| **Styling** | Bootstrap 5 utility classes + a small custom CSS file per app (`static/css/pos.css`, `static/css/dashboard.css`) | Avoids fighting Bootstrap's defaults while allowing brand-specific touches (Jorge's Café colors/logo) without a full custom design system. |
| **Charts** | Chart.js | Explicitly named in the manuscript (Table 3-3, Fig. 3-19/3-25/3-28 etc. all describe Chart.js-style dashboards). Lightweight, no build step, works directly against DRF JSON endpoints. |
| **AJAX/HTTP** | Native `fetch()` | No need for Axios/jQuery — modern `fetch` is sufficient for the POS and dashboard AJAX calls, one less dependency. |

## 2. Backend

| Layer | Choice | Why |
|---|---|---|
| **Runtime** | Python 3.11+ | Current stable, well-supported by Django 5.x and all required AI libraries (statsmodels, pandas, numpy, scikit-learn). |
| **Framework** | Django 5.x + Django REST Framework | Explicitly named in the manuscript (Table 3-3: "Backend architecture, ORM, CSRF protection, and RBAC"). Django's batteries-included auth, admin panel, and ORM map directly onto the RBAC/PBKDF2/2FA requirements without extra libraries. DRF layers cleanly on top for the JSON API needs. |
| **Background jobs** ✅ Revised | **APScheduler with a persistent job store** (`SQLAlchemyJobStore` pointed at the same MySQL database) + job-failure email alerts to the Owner/Admin | Still avoids taking on Redis/Celery as extra infrastructure for a 3-branch system. But since this is a **live production deployment**, a job store that only lives in memory is a real risk — a server restart, redeploy, or crash would silently drop any pending scheduled job (e.g., that night's forecast or the KaHero batch import), and nobody would notice until someone asks "why hasn't the dashboard updated." Persisting job state to MySQL means schedules survive restarts, and email alerts on job failure mean a broken job gets noticed immediately instead of discovered days later. Celery + Redis remains the documented upgrade path if job volume or branch count grows significantly. |

## 3. Database

| Layer | Choice | Why |
|---|---|---|
| **Production DB** | MySQL 8.x | Explicitly named in the manuscript (Table 3-3) as the production centralized relational database. |
| **Dev/prototyping DB** | SQLite | Explicitly named in the manuscript for dev/prototyping only — zero-setup for local development, switched via Django's `DATABASES` config per environment. |
| **ORM** | Django ORM | Comes free with Django; handles migrations, PK/FK constraints, and query building without a separate ORM library (no need for SQLAlchemy). |

## 4. Authentication

| Layer | Choice | Why |
|---|---|---|
| **Core auth** | `django.contrib.auth` (session-based) | Built-in, PBKDF2 password hashing by default — directly satisfies the manuscript's stated security requirement with zero custom crypto code. |
| **RBAC** | Custom `Role` + `Branch` models, enforced via Django permission classes / decorators + DRF permission classes | Django's built-in groups/permissions are extended with a `branch_id` scope so the same role (e.g., `BRANCH_STAFF`) is automatically restricted to their own branch's data — satisfies the Branch Selection / data-isolation requirement from the manuscript. |
| **Cashier PIN** | Custom lightweight PIN model + session flag (not a full Django login) | Matches the manuscript's Cashier PIN Authentication Screen — a fast numeric unlock layered on top of an already-logged-in branch session, not a second full auth system. |
| **2FA (Admin/Owner)** | `django-otp` (TOTP) | Well-maintained, integrates directly with Django auth, satisfies the System Configuration screen's 2FA toggle. |

## 5. Storage

| Layer | Choice | Why |
|---|---|---|
| **File storage** ✅ Revised | **Local `media/` storage via Django's default `FileSystemStorage`** on PythonAnywhere's persistent disk, **plus a scheduled nightly backup job** (copies the `media/` folder and a `mysqldump` of the database to a secondary location — e.g., a private cloud storage bucket or off-server destination used purely for backup, not as the live storage backend) | With this now being a live system holding real KaHero export files, real receipts, and real transaction history, "persistent disk" alone isn't sufficient — disk failure or accidental deletion would mean permanent data loss with no recovery path. Rather than migrating the *live* storage layer to S3 (more setup/cost, more moving parts for the team to manage), the durability problem is solved directly with automated backups, which is the actual risk being protected against. `django-storages` + S3 remains a valid alternative if you'd rather have versioned cloud storage as the primary store instead of local-disk-plus-backup — flag if you'd prefer that route. |

## 6. State Management

| Layer | Choice | Why |
|---|---|---|
| **Frontend state** | Plain JS module-scoped state + DOM as source of truth (no Redux/Zustand/etc.) | There's no SPA client-side routing or complex shared state here — each page/template owns its own small amount of state (current order in progress, current filter selection). Introducing a state-management library would be solving a problem this architecture doesn't have. |
| **Server-side "state"** | Django sessions (for login, branch selection, PIN-unlock flag, shift status) | Sessions are the natural fit for "is this cashier's PIN currently unlocked for this branch" type state — no need for a separate cache layer at this scale (though Redis could double as a session backend later if introduced for Celery). |

## 7. Version Control

| Layer | Choice | Why |
|---|---|---|
| **VCS** | Git | Explicitly named in the manuscript (Table 3-3). |
| **Hosting** | GitHub | Explicitly named in the manuscript (Table 3-3) — also gives the panel/adviser a reviewable commit history and PR trail, useful for demonstrating individual contribution during defense. |
| **Branching/commit strategy** | Detailed in Phase 5 (not repeated here) | — |

## 8. Deployment / Hosting

| Layer | Choice | Why |
|---|---|---|
| **Hosting** 🔧 | **PythonAnywhere** as the default recommendation over Render | Both are explicitly named in the manuscript (Table 3-3), but PythonAnywhere has **persistent disk by default** (simplifies file storage — see §5) and a simpler MySQL-included setup, which matters more for a small team than Render's more modern container-based deploys. Render is a fine alternative if you're more comfortable with it or want auto-deploy-from-GitHub — flag if you'd rather default to Render instead. |
| **Static/media serving** | WhiteNoise (for static files) + storage backend from §5 (for media/uploads) | Avoids needing a separate CDN/Nginx config, while still following the "don't serve static files through Django's dev server in production" best practice. |
| **Environments** ✅ Revised | **Staging + Production split** — two separate PythonAnywhere deployments (or one PythonAnywhere account with two web apps if the plan allows it), each with its own MySQL database and its own `.env` | Now that branches will be processing real daily transactions, pushing an untested change straight to the system cashiers are actively using is a real operational risk (a bad migration or POS bug could stop sales mid-shift). All changes get deployed to staging first, verified there, then promoted to production — ideally during low-traffic hours (e.g., after closing). This is a reversal of the original capstone-only recommendation (single environment), made specifically because deployment is now mandatory and real income depends on it. |

## 9. Testing Framework

| Layer | Choice | Why |
|---|---|---|
| **Backend unit/integration tests** | Django's built-in `TestCase` (unittest-based) + `pytest-django` | `pytest-django` gives cleaner fixtures and better output than plain `unittest`, while still running against Django's test database machinery — widely used in Django projects, easy for a small team to pick up. |
| **API tests** | DRF's `APITestCase` | Purpose-built for testing DRF endpoints (status codes, serializer validation, permission checks). |
| **Frontend** | Manual/UAT testing (per manuscript §3.8: Functional, Usability, UAT, Performance testing) rather than an automated JS test framework | The manuscript's own Testing and Evaluation Procedure (§3.8) describes Functional, Usability, UAT, and Performance testing done manually with real users (Likert-scale evaluation) — this is standard for a capstone of this scope and no automated frontend test suite (Jest/Cypress) is called for in the paper. Can be added later if you want stronger regression coverage on the POS/dashboard JS. |
| **AI module validation** | Simple accuracy/error-metric scripts (e.g., MAE/RMSE against holdout historical data) for the ARIMA model, run manually during development, not as part of CI | Matches the manuscript's framing of AI outputs needing validation against historical datasets (§3.9 Responsible Use of AI) without over-engineering a formal MLOps pipeline for a capstone. |

## 10. Analytics (project instrumentation, not the business-analytics dashboards)

Not explicitly required by the manuscript beyond the AI dashboards themselves — no separate product-analytics tool (e.g., Mixpanel) is warranted here. Skipping this as a distinct stack item unless you want basic uptime/error monitoring (see below).

**One addition worth flagging though it wasn't in your original Phase 3 checklist:** basic error monitoring (e.g., Django's built-in email-on-500 admin alerts, or a free tier of Sentry) — cheap to add and genuinely useful once branches are relying on this daily. Let me know if you want it included or left out for scope reasons.

---

## Summary of Defaults I Picked (flag any you want changed)

| Decision | Default chosen | Status |
|---|---|---|
| Background job scheduler | APScheduler with persistent (MySQL-backed) job store + failure alerts | ✅ Revised for production |
| File storage | Local disk (`FileSystemStorage`) + automated nightly backups of media + DB | ✅ Revised for production |
| Hosting | PythonAnywhere (Render as alternative) | Unchanged |
| Staging vs. single environment | **Staging + Production split**, each with its own DB and `.env` | ✅ Revised for production |

Ready to move to **Phase 4 — Project Structure** whenever you are — the folder layout will now account for the staging/production split (separate settings/env files) from the start.
