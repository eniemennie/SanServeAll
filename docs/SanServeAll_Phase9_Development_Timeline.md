# SanServeAll — Phase 9: Development Timeline

**Assumption stated up front:** the original instructions didn't specify a total duration, so I'm proposing a **16-week schedule** (roughly one academic term) sized to the module list from Phase 1/4 and a 3-person team. This groups naturally into the manuscript's own four implementation stages (§3.7: Database Foundation → Transactional Core → Production & Analytics → Intelligence & DSS) plus dedicated integration/UAT/deployment weeks at the end, since this is now a mandatory real deployment, not just a defended prototype. Adjust week count freely if your actual term/defense schedule is shorter or longer — the module order and dependencies below hold regardless of exact pacing.

---

## Stage A: Foundation (Weeks 1–3)

### Week 1 — Project Setup
- **Objectives:** Get every team member's environment working; establish the repo exactly as scaffolded in Phases 4–8.
- **Deliverables:** Repo initialized with full folder structure (Phase 4), `.gitignore`/branch protection/CI workflow live (Phases 5, 8), all config files in place, `.env.example` files complete, everyone can run `python manage.py runserver` locally against SQLite.
- **Modules touched:** `config/` (settings, urls, scheduler skeleton), `core/` (base models: `TimestampedModel`, `BranchScopedModel`).
- **Testing:** CI pipeline itself verified (a trivial passing test confirms `ci.yml` actually runs on PR).

### Week 2 — Database Foundation
- **Objectives:** Stand up the centralized schema for `Branch`, `Role`, `User`, and register all planned models across apps as empty skeletons (matches manuscript Stage 1).
- **Deliverables:** Migrations for `accounts` app complete; `database/seed_data.sql` loaded (3 branches, roles, starter product catalog); Django admin usable for manual data inspection.
- **Modules touched:** `accounts/` (models only, no views yet).
- **Testing:** `test_models.py` for `User`/`Role`/`Branch` (constraints, `KAHERO_BRANCH` config resolves correctly to Alangilan).

### Week 3 — Authentication & RBAC
- **Objectives:** Full login flow: Login/Start screen, Branch Selection, Cashier PIN, Admin Login, RBAC enforcement, 2FA for Owner/Admin.
- **Deliverables:** All auth-related screens from Phase 1 (Figs. 3-9, 3-10, 3-11, 3-18) functional end-to-end; branch-scoping middleware enforced.
- **Modules touched:** `accounts/` (services, views, permissions).
- **Testing:** `test_services.py` (PIN verification logic), `test_views.py` (login/branch-selection flows), manual functional test per role (owner/staff/commissary each see correctly scoped data).

---

## Stage B: Transactional Core (Weeks 4–7)

### Week 4 — POS Module, Part 1
- **Objectives:** Core ordering flow for native-POS branches (Batangas City, Lipa City).
- **Deliverables:** POS Ordering Screen, Add Custom Product, Order Customization (Figs. 3-12, 3-13, 3-14).
- **Modules touched:** `pos/` (models, serializers, initial views).
- **Testing:** `test_models.py` for `SalesTransaction`/`SalesItem`; manual functional testing of order-building UI.

### Week 5 — POS Module, Part 2 + Inventory Hook
- **Objectives:** Payment processing, receipt generation, and the real-time inventory deduction that a completed sale triggers.
- **Deliverables:** Payment Processing + Transaction Receipt screens (Figs. 3-16, 3-17); `deduct_inventory()` service wired to sale completion for native-POS branches only.
- **Modules touched:** `pos/services.py`, `inventory/services.py` (deduction logic only, full inventory UI comes next week).
- **Testing:** `test_services.py` for `process_sale()` and `deduct_inventory()` (including the negative case: KaHero-branch sales should NOT trigger this path).

### Week 6 — Inventory Module
- **Objectives:** Full inventory visibility and management.
- **Deliverables:** Inventory Monitoring, Product Inventory Management, Finished Goods Monitoring, Materials Tracking, Stock and Resource Management screens (Figs. 3-20, 3-27, 3-32, 3-33, 3-34); low-stock alert triggering.
- **Modules touched:** `inventory/` (full).
- **Testing:** `test_services.py` for reorder-threshold logic and alert triggering; manual UAT-style pass focused on stock accuracy against Table 3-1's original "inventory inaccuracy" problem.

### Week 7 — KaHero Batch-Import Pipeline (Alangilan)
- **Objectives:** Build the confirmed batch-import path for the Alangilan branch.
- **Deliverables:** File upload screen, `KaheroImportBatch` audit model, CSV/Excel parser, ingestion into `pos`/`inventory` models, import-status view; Batch Processing Analytics Dashboard (Fig. 3-23) reads from this data.
- **Modules touched:** `kahero_integration/` (full).
- **Testing:** `test_parsers.py` (malformed file handling, empty file, encoding edge cases), `test_services.py` (full ingest-then-verify-inventory-updated flow).

---

## Stage C: Production & Analytics (Weeks 8–9)

### Week 8 — Production/Commissary Module
- **Objectives:** Commissary data entry and its link to branch inventory.
- **Deliverables:** Production recording, ingredient usage tracking, Batch Management Interface (Fig. 3-24), production-to-inventory reconciliation.
- **Modules touched:** `production/`.
- **Testing:** `test_services.py` for reconciliation logic; manual test with commissary-role account.

### Week 9 — Analytics Module (Read-Side Reporting)
- **Objectives:** Consolidated, branch-filterable reporting dashboards — everything that reads existing data, not the AI-generated layer yet.
- **Deliverables:** Analytics Dashboard, Sales Analytics, Product Performance Monitoring, Resource Consumption Analytics, Operational Performance screens (Figs. 3-19, 3-28–3-31); Branch Filter Dropdown (Fig. 3-22).
- **Modules touched:** `analytics/`.
- **Testing:** `test_services.py` for aggregation query correctness against known seed data; visual/manual check of Chart.js rendering.

---

## Stage D: Intelligence & Decision Support (Weeks 10–11)

### Week 10 — AI Module, Part 1 (Forecasting Core)
- **Objectives:** Data prep pipeline and ARIMA forecasting.
- **Deliverables:** `data_prep.py` (pandas/numpy cleaning of historical sales), `arima_model.py` (statsmodels wrapper), first working forecast stored in `Forecast` model, `jobs.py` entrypoint registered in `config/scheduler.py` with persistent job store.
- **Modules touched:** `forecasting/ml/`, `forecasting/jobs.py`, `config/scheduler.py`.
- **Testing:** `test_arima_model.py` — MAE/RMSE against a holdout slice of seed/demo sales data (per Phase 3's validation approach); manual check that the job survives a local server restart (persistent job store working as intended).

### Week 11 — AI Module, Part 2 (Risk Classification + NL Insights + Dashboards)
- **Objectives:** Complete the AI feature set and its dashboards.
- **Deliverables:** `risk_classifier.py` (scikit-learn), `insight_generator.py` (Claude/OpenAI API call for natural-language recommendations), AI-Powered Decision Support Interface, AI Forecasting Dashboard, AI Resource Management Dashboard (Figs. 3-21, 3-25, 3-26).
- **Modules touched:** `forecasting/` (full).
- **Testing:** `test_risk_classifier.py`, `test_services.py` for the orchestration layer; manual review of generated NL insights for reasonableness (matches §3.9 "Responsible Use of AI" — outputs validated against historical patterns, framed as recommendations not decisions).

---

## Stage E: Settings, Hardening, and Loose Ends (Week 12)

### Week 12 — System Settings + Remaining Hardware/Polish Items
- **Objectives:** Admin configuration screens and any remaining open items from earlier phases.
- **Deliverables:** System Settings, System Configuration screens (Figs. 3-35, 3-36); resolve the Bluetooth receipt printer integration (per the standalone note — implement whichever option was decided: RawBT bridge, WebView wrapper, or Wi-Fi printing); finalize 2FA enrollment flow end-to-end.
- **Modules touched:** `accounts/` (2FA, settings), new `pos/printing.py` if Bluetooth integration proceeds.
- **Testing:** Manual functional test of every settings toggle; end-to-end print test on the actual Android tablet + printer hardware.

---

## Stage F: Integration, UAT, and Deployment (Weeks 13–16)

### Week 13 — Full Integration Testing
- **Objectives:** Test the system as a whole rather than module-by-module — cross-branch data flow, KaHero batch import alongside live native-POS sales, AI jobs running against real accumulated data.
- **Deliverables:** All `pytest` suites passing in CI; manual end-to-end walkthroughs per role (owner, branch staff, commissary staff).
- **Modules touched:** All.
- **Testing:** Functional Testing + Performance Testing per manuscript §3.8 (simulate concurrent multi-branch usage against staging's MySQL instance).

### Week 14 — User Acceptance Testing (UAT)
- **Objectives:** Real end-users (owner, branch managers, cashiers) exercise the system per §3.8's UAT procedure.
- **Deliverables:** Completed Likert-scale questionnaires (Table 3-4), weighted-mean/standard-deviation analysis, documented feedback themes (Descriptive Analysis per §3.8).
- **Modules touched:** N/A (evaluation activity, not development).
- **Testing:** UAT is the test — output feeds directly into any final fixes in Week 15.

### Week 15 — Staging Deployment & Fixes
- **Objectives:** Deploy to the staging PythonAnywhere environment (if not already continuously deployed there), address UAT feedback, verify backup/restore procedure actually works.
- **Deliverables:** Staging fully mirrors intended production behavior; `scripts/backup_media_and_db.sh` tested with an actual restore drill, not just "the script ran."
- **Modules touched:** Whichever modules UAT feedback touched; `deployment/`.
- **Testing:** Regression pass on affected modules; backup/restore drill counted as a test in its own right given the mandatory-deployment stakes.

### Week 16 — Production Launch
- **Objectives:** Promote `staging` → `main` (Phase 5 PR-based promotion flow), deploy to production, train end-users, go live across all three branches.
- **Deliverables:** Tagged `v1.0.0` production release (Phase 5 versioning); user training completed for all roles; system operating as the primary platform per the manuscript's Launch phase description.
- **Modules touched:** N/A (deployment activity).
- **Testing:** Production smoke test immediately post-deploy (login, one test sale per branch type, one KaHero batch import, dashboard loads); monitoring of the first scheduled AI job run in production.

---

## Summary Table

| Week | Stage | Focus |
|---|---|---|
| 1 | Foundation | Project/environment setup |
| 2 | Foundation | Database foundation |
| 3 | Foundation | Auth & RBAC |
| 4–5 | Transactional Core | POS module |
| 6 | Transactional Core | Inventory module |
| 7 | Transactional Core | KaHero batch-import (Alangilan) |
| 8 | Production & Analytics | Commissary/production |
| 9 | Production & Analytics | Analytics dashboards |
| 10–11 | Intelligence & DSS | AI forecasting + risk + NL insights |
| 12 | Hardening | Settings + Bluetooth printer + 2FA |
| 13 | Integration | Full integration testing |
| 14 | UAT | Real end-user evaluation |
| 15 | Deployment | Staging fixes + backup drill |
| 16 | Launch | Production go-live |

---

Ready for **Phase 10 — Pre-Development Checklist** whenever you'd like to continue.
