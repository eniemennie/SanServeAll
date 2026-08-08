# SanServeAll — Phase 10: Pre-Development Checklist & Risk Assessment

This is the final gate before writing any application code. The original checklist template included Firebase-specific items that don't apply to this stack (Phases 2–3 confirmed Django/MySQL/PythonAnywhere, not Firebase) — those are adapted below to their real equivalents rather than kept as dead checkboxes.

---

## 1. Planning & Documentation

- [ ] Phase 1 (Project Analysis) reviewed and confirmed by all 3 team members
- [ ] Phase 2 (System Architecture) diagram and layer breakdown reviewed
- [ ] Phase 3 (Technology Stack) — all 🔧/✅ flagged decisions confirmed, no lingering "pick one" items
- [ ] Phase 4 (Project Structure) folder layout agreed upon
- [ ] Phase 5 (GitHub Strategy) branch/commit/PR conventions agreed upon by all members
- [ ] Phase 6 (Environment Setup) completed by **every** team member individually (not just one person's machine)
- [ ] Phase 7 (Development Standards) — naming/formatting tools (`black`, `isort`, `flake8`, `.editorconfig`) actually installed and verified working, not just documented
- [ ] Phase 8 (Configuration Files) — all files from the Phase 8 summary checklist actually created in the repo
- [ ] Phase 9 (Development Timeline / Gantt) shared with and acknowledged by the adviser
- [ ] Standalone Bluetooth printer note — decision made (RawBT / WebView bridge / Wi-Fi printing) before Week 12 arrives, not left open

## 2. Repository & CI/CD

- [ ] GitHub repository created, all 3 members added as collaborators
- [ ] `main` and `staging` branch protection rules active (PR required, 1+ approval, CI status check required)
- [ ] `.github/workflows/ci.yml` runs successfully on a test PR (verified, not assumed)
- [ ] `.gitignore` confirmed to actually exclude `.env`, `media/`, `db.sqlite3`, `staticfiles/`, `database/backups/*`
- [ ] `.env.example` (and staging/production equivalents) committed and complete
- [ ] No real secrets ever committed in initial scaffolding commits (double-check history before pushing)

## 3. Environment & Hosting

- [ ] Every team member can run the full local setup sequence from Phase 6 §7 without errors
- [ ] PythonAnywhere account(s) created, with **separate web apps and separate MySQL databases** for staging and production (Phase 3 revision)
- [ ] Staging WSGI config (`deployment/staging/wsgi_staging.py`) deployed and reachable
- [ ] Production WSGI config (`deployment/production/wsgi_production.py`) prepared (deploy target ready, even if not yet receiving traffic)
- [ ] APScheduler persistent job store confirmed working: schedule a test job, restart the app, confirm the job survives and still fires
- [ ] `scripts/backup_media_and_db.sh` tested end-to-end **including an actual restore**, not just "the script exits 0"
- [ ] Claude API key / OpenAI API key obtained and stored only in the appropriate `.env` (never committed)

## 4. Database

- [ ] ERD (Phase 1 §6 / manuscript Fig. 3-7) finalized, including the previously-unnamed entities identified as gaps (`Branch`, `ProductionRecord`/`Batch`, KaHero staging table)
- [ ] Initial migrations for `accounts` app (User, Role, Branch) written and reviewed
- [ ] `database/seed_data.sql` prepared with the 3 real branches, roles, and a representative starter product catalog
- [ ] Confirmed with client: which branch is KaHero vs. native POS (**resolved: Alangilan = KaHero, Batangas City & Lipa City = native**) — reflected consistently in seed data and settings, not just in docs

## 5. UI / Design

- [ ] All 28 screens from Phase 1 (Figs. 3-9–3-36) reviewed against the actual manuscript figures (not just captions, since original image assets weren't extractable from the uploaded text — confirm the team has the real image files/DOCX)
- [ ] Wireframes/mockups approved by adviser and, ideally, informally previewed with the client (Ms. Rhona / Mr. Jape per the manuscript's acknowledgment section)
- [ ] Bootstrap 5 + Chart.js color palette/branding direction agreed (matches Jorge's Café's actual branding, not a generic template look)

## 6. Team & Process

- [ ] Module ownership assigned across the 3 members (recommend splitting along the Phase 9 stage boundaries — e.g., one member owns POS+Inventory, one owns Production+Analytics, one owns Forecasting/AI+KaHero — with shared responsibility for `accounts`/auth since everything depends on it)
- [ ] Weekly check-in cadence agreed (standups, or at minimum a weekly written status matching the Phase 9 timeline)
- [ ] Adviser validation-gate schedule confirmed (per manuscript §3.8 "Adviser Monitoring Requirement" — periodic reviews, sign-off before each stage proceeds)

## 7. Security & Compliance

- [ ] RBAC roles (`OWNER_ADMIN`, `BRANCH_STAFF`, `COMMISSARY_STAFF`) reviewed against real staff assignments at Jorge's Café
- [ ] PBKDF2 (Django default) confirmed as the active password hasher in settings (not accidentally left on a weaker dev-only hasher)
- [ ] 2FA (`django-otp`) enrollment flow planned for Owner/Admin accounts before go-live
- [ ] Data Privacy Act (RA 10173) compliance notes from the manuscript's Ethical Considerations (§3.9) reflected in actual access-control implementation, not just paper policy
- [ ] Client consent (already secured per manuscript §3.9) — confirm this covers the *live production* deployment specifically, not only the original academic data-collection/study phase, since "mandatory for deployment" changes the nature of the engagement beyond a capstone study

---

## Risk Assessment & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Manuscript's Alangilan/Batangas City contradiction resurfaces** (e.g., someone on the team references an older doc version) | Medium | High — wrong branch config breaks the core sync logic | Config is centralized as a single `KAHERO_BRANCH` value (Phase 2/4) rather than scattered conditionals; seed data and onboarding docs state the confirmed answer explicitly (Alangilan) in one place. |
| **APScheduler job silently stops after a server restart/crash** | Medium | High — a missed nightly forecast or KaHero import could go unnoticed for days | Persistent `SQLAlchemyJobStore` + failure-alert emails (Phase 3 revision) — verify this is tested, not just configured, before go-live (checklist §3 above). |
| **Real business data loss** (disk failure, accidental deletion on PythonAnywhere) | Low–Medium | Very High — real transaction/inventory history, not recoverable from a capstone reset | Automated nightly backups + a *tested* restore drill (not just "the script ran") — this is the single most important item in the whole checklist given mandatory production status. |
| **Bluetooth printer integration slips past Week 12 unresolved** | Medium | Medium — cashiers fall back to no functional receipt printing at launch | Standalone note already flags 3 concrete options; decision forced explicitly before Week 12 in the timeline rather than left ambiguous. |
| **KaHero export format changes or is inconsistent between exports** (real-world messy data, since it's manually exported by staff) | Medium | Medium — batch ingestion fails or silently imports bad data | `kahero_integration/parsers.py` validates row-level structure and rejects/flags malformed rows rather than silently ingesting them; `KaheroImportBatch` audit model records per-import success/failure counts for staff/admin review. |
| **Small team (3 people) + mandatory production stakes = high individual dependency risk** (illness, exam conflicts, etc. during the 16-week window) | Medium | Medium–High | Module ownership assignment (checklist §6) still requires each member to be reasonably familiar with at least one other module's code via PR review — avoids single points of failure on `forecasting/` or `kahero_integration/` specifically, since those are the least "standard CRUD" and hardest to onboard into under time pressure. |
| **AI forecast accuracy disappoints real users** (ARIMA is a relatively simple model; real sales data may be noisier than assumed) | Medium | Medium | Manuscript itself frames this as a known limitation (§1.3 Scope) — outputs are explicitly presented as decision *support*, not automated decisions (§3.9 Responsible Use of AI); MAE/RMSE validation against holdout data (Phase 9, Week 10) catches egregious inaccuracy before go-live, and NL-insight framing sets appropriate user expectations ("recommendation," not "guarantee"). |
| **Staging/production drift** (a hotfix applied to production forgotten in staging) | Low–Medium | Medium | Phase 5's hotfix branch strategy explicitly merges to *both* `main` and `staging` — enforce this as a hard rule in PR review, not just documentation. |
| **Adviser/panel expects different tooling than what was chosen** (e.g., Celery instead of APScheduler) | Low–Medium | Low–Medium (rework cost, not data risk) | Each Phase 3 decision is documented with its reasoning and named alternative — makes it straightforward to justify the choice in defense, or swap to the alternative early if adviser feedback comes back before Week 10 (forecasting/scheduler work). |

---

## Final Go/No-Go

Development should not begin until:
1. Every checkbox in §1–§7 above is checked, **or** explicitly deferred with a written reason and owner.
2. The adviser has signed off on Phases 1–9 as a package (not phase-by-phase in isolation), since several decisions (e.g., mandatory production status) revised earlier phases after the fact — a single consolidated sign-off avoids building against a since-superseded version of any document.
3. The backup/restore drill (§3) has been physically demonstrated to the team, not just described.

---

This closes the Phase 1–10 planning series. From here, development proceeds per the Phase 9 timeline, Week 1 (Project Setup).
