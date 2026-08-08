# SanServeAll — Phase 5: GitHub Strategy

Designed for a 3-person team (Aguila, Banaag, Catapang) building a system that's now confirmed for mandatory production deployment — the branch/release strategy is intentionally a bit more disciplined than a typical capstone-only repo, to match the staging→production promotion flow from Phase 3.

---

## 1. Repository Structure

**Single monorepo**, not separate repos per app/module.

```
sanserveall/                 ← repository root (matches Phase 4 top-level layout exactly)
├── backend/
├── docs/
├── database/
├── deployment/
├── .github/
├── .gitignore
├── .env.example
└── README.md
```

**Why one repo:** the Django project is a single deployable unit (one `manage.py`, one settings tree) — splitting `apps/` into separate repos would only add submodule/versioning overhead with no real isolation benefit, since every app shares the same database and deployment target. A monorepo also gives the adviser/panel one place to review the full commit history for individual contribution assessment.

---

## 2. Branch Strategy

```
main            ← always deployable to PRODUCTION. Protected. Only updated via PR from staging or a hotfix branch.
staging         ← always deployable to STAGING. Protected. Feature branches merge here first.
feature/*       ← one branch per feature/module, branched off staging
bugfix/*        ← one branch per non-urgent bug fix, branched off staging
hotfix/*        ← urgent production fix, branched off main, merged to BOTH main and staging
```

### Flow

```
feature/pos-payment-screen ──┐
bugfix/inventory-alert-off-by-one ──┤──► staging ──► (tested/verified) ──► main ──► deploy to PRODUCTION
feature/arima-forecast-job ──┘            │
                                           └──► deploy to STAGING (auto or manual)

hotfix/pos-crash-on-checkout ──► main (deploy immediately) AND staging (keep in sync)
```

### Naming convention

| Type | Pattern | Example |
|---|---|---|
| Feature | `feature/<app>-<short-description>` | `feature/pos-cashier-pin-auth` |
| Bug fix | `bugfix/<app>-<short-description>` | `bugfix/inventory-reorder-threshold` |
| Hotfix | `hotfix/<short-description>` | `hotfix/kahero-import-crash` |
| Chore/infra | `chore/<short-description>` | `chore/update-requirements` |

Rules:
- Never commit directly to `main` or `staging` — both are protected branches (require PR + at least one reviewer approval, from Phase 5's GitHub branch protection settings).
- `feature/*` and `bugfix/*` always branch from **`staging`**, not `main` — this guarantees `main` only ever receives already-tested code.
- `hotfix/*` is the one exception: branches from `main` (to fix what's actually live right now), then gets merged into **both** `main` and `staging` so staging doesn't drift out of sync with the emergency fix.
- Delete feature/bugfix branches after merge to keep the branch list clean.

---

## 3. Commit Message Convention

**Conventional Commits** format:

```
<type>(<scope>): <short summary>

[optional body]
[optional footer, e.g. "Closes #12"]
```

| Type | Use for |
|---|---|
| `feat` | New feature/module functionality |
| `fix` | Bug fix |
| `docs` | Documentation-only changes (including `docs/` phase files) |
| `style` | Formatting only, no logic change |
| `refactor` | Code change that isn't a fix or a feature |
| `test` | Adding/updating tests |
| `chore` | Tooling, dependencies, config, CI |
| `perf` | Performance improvement |

**Scope** = the app/module affected (`pos`, `inventory`, `accounts`, `forecasting`, `kahero`, `analytics`, `production`, `deploy`).

**Examples:**
```
feat(pos): add order customization for size and sugar level
fix(inventory): correct low-stock threshold comparison operator
docs(phase5): add GitHub branching strategy
chore(deploy): add staging WSGI config
test(forecasting): add MAE/RMSE checks for ARIMA holdout validation
hotfix(kahero): fix crash on empty CSV upload
```

Why this convention: it makes the commit log itself scannable by module and type — useful both for day-to-day review and for writing the capstone's own documentation/changelog later, since `feat`/`fix` commits map directly onto the functional requirements table from Phase 1.

---

## 4. Pull Request Workflow

1. Branch off `staging` (or `main` for a hotfix).
2. Commit using the convention above; push the branch.
3. Open a PR with:
   - **Title** matching the commit convention (e.g., `feat(pos): cashier PIN authentication`)
   - **Description** template (see below)
   - Linked issue, if one exists
4. At least **one other team member reviews and approves** before merge (3-person team → simple peer review, not a large approval chain).
5. CI must pass (Phase 5's GitHub Actions — runs `pytest` per Phase 3's testing stack) before merge is allowed.
6. Merge strategy: **Squash and merge** into `staging`/`main` — keeps the target branch's history clean (one commit per feature/fix) while the feature branch itself can have messy in-progress commits.
7. Delete the source branch after merge.

**PR description template** (`.github/pull_request_template.md`):
```markdown
## What does this PR do?


## Related module (Phase 1 FR reference)
FR-0_ / N/A

## How was this tested?
- [ ] Ran locally against SQLite
- [ ] Unit tests added/updated
- [ ] Manually tested on staging

## Checklist
- [ ] No secrets/credentials committed
- [ ] Migrations included if models changed
- [ ] Docs updated if behavior changed
```

### Staging → Production promotion

Since `main` = production and `staging` = staging (Phase 3 decision), promotion is itself a PR: **`staging` → `main`**, opened once a batch of features has been verified on the staging deployment, reviewed the same way as any other PR, and merged during low-traffic hours (matches the Phase 3 rationale for not deploying to production mid-shift).

---

## 5. `.gitignore`

Already defined in Phase 4 §4 — reproduced here for completeness since it's part of the GitHub strategy:

```gitignore
# Secrets
.env
.env.*
!.env.example
!deployment/**/env.*.example

# Python
__pycache__/
*.pyc
.venv/
venv/

# Django
backend/media/
backend/logs/
db.sqlite3
staticfiles/

# Backups (never committed, only ever local/temporary)
database/backups/*
!database/backups/.gitkeep

# Editor/OS cruft
.vscode/
.idea/
.DS_Store
```

---

## 6. Versioning

**Semantic Versioning (SemVer)**: `MAJOR.MINOR.PATCH` (e.g., `v1.2.0`), tagged on `main` at each production release.

- **MAJOR** — breaking changes to data model or a full module rewrite (rare post-launch).
- **MINOR** — new functional requirement shipped (e.g., v1.1.0 = AI Forecasting Dashboard goes live).
- **PATCH** — bug fixes, small tweaks (e.g., v1.1.1 = fixed inventory threshold bug).

Each tag on `main` corresponds to an actual production deployment — gives you (and your panel) a clear "what was live when" history, and doubles as a changelog source (`git log v1.0.0..v1.1.0 --oneline`) for your final documentation (Phase 9).

Pre-launch development can use `v0.x.y` tags for internal milestones (e.g., `v0.1.0` = auth + branch selection working end-to-end) if you want incremental tagging before the first real production release (`v1.0.0`).

---

## 7. Branch Protection Settings (GitHub repo settings, not code)

Applied to both `main` and `staging`:
- Require pull request before merging (no direct pushes)
- Require at least 1 approval
- Require status checks to pass (CI test run) before merge
- Require branches to be up to date before merging
- (Optional, recommended once live) Restrict who can push to `main` to reduce accidental production merges

---

Ready for **Phase 6 — Environment Setup** whenever you'd like to continue.
