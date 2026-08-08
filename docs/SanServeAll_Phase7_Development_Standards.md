# SanServeAll — Phase 7: Development Standards

Branch naming and commit naming were already fully defined in **Phase 5** — reproduced here only as a quick cross-reference so this document is a complete standards checklist on its own. Everything else below is new.

---

## 1. Python / Django Code Naming Conventions

Follows **PEP 8**, enforced automatically via `black` + `isort` + `flake8` (already in `requirements/development.txt`, Phase 6).

| Element | Convention | Example |
|---|---|---|
| **Variables / functions** | `snake_case` | `calculate_reorder_threshold()`, `low_stock_items` |
| **Classes (models, serializers, services)** | `PascalCase` | `SalesTransaction`, `InventoryRiskScore`, `KaheroImportBatch` |
| **Constants** | `UPPER_SNAKE_CASE` | `KAHERO_BRANCH`, `DEFAULT_REORDER_THRESHOLD` |
| **Private/internal helpers** | leading underscore | `_validate_row(row)` |
| **Django app names** | lowercase, singular-domain, matches Phase 4 folder names | `pos`, `inventory`, `forecasting`, `kahero_integration` |
| **Service functions** | verb-first, describes the business action | `process_sale()`, `deduct_inventory()`, `run_forecast_job()` |
| **Test files/functions** | `test_<unit_under_test>.py` / `test_<behavior>()` | `test_services.py` → `test_deduct_inventory_reduces_stock_by_quantity()` |

**Docstrings:** every `services.py` function gets a one-to-three-line docstring stating *what it does* and *what it doesn't do* (especially important for things like "this does NOT trigger a synchronous forecast run" — matching the Phase 2 decoupling rule). Google-style docstrings preferred for consistency:

```python
def deduct_inventory(sale: SalesTransaction) -> None:
    """Deduct sold quantities from Inventory for a completed sale.

    Only applies to native-POS branches; KaHero-branch (Alangilan) inventory
    changes are applied separately via the batch-import pipeline.
    """
```

---

## 2. Folder Naming

Already fully specified in **Phase 4** (`apps/`, `config/`, `templates/`, etc.) — the standard going forward is: **all folders lowercase, words separated by underscores if multi-word** (`kahero_integration`, not `KaheroIntegration` or `kahero-integration`), matching Python package-naming rules (hyphens aren't valid in importable Python package names).

---

## 3. "Component" Naming (Templates & JS)

Since the frontend is server-rendered Django templates + vanilla JS (no React/Vue components per Phase 3), "components" here means **template partials** and **JS modules**.

| Element | Convention | Example |
|---|---|---|
| **Full page templates** | `snake_case.html`, matches the view/URL name | `pos_ordering.html`, `analytics_dashboard.html` |
| **Reusable template partials** (includes) | prefixed with underscore, stored in a `partials/` subfolder per app | `templates/pos/partials/_order_summary.html` |
| **JS files** | `snake_case.js`, one file per screen/concern | `pos.js`, `dashboard.js`, `branch_filter.js` |
| **JS function names** | `camelCase` (JS convention, distinct from Python's `snake_case` — this split is intentional and matches each language's own idiom) | `renderSalesChart()`, `unlockCashierPin()` |
| **CSS classes** | `kebab-case`, BEM-influenced for anything custom beyond Bootstrap utilities | `.pos-order-summary`, `.pos-order-summary__total` |

---

## 4. API Naming (DRF endpoints)

| Rule | Example |
|---|---|
| All endpoints versioned and namespaced by app | `/api/v1/pos/transactions/`, `/api/v1/inventory/products/` |
| Plural nouns for collections, no verbs in the URL (REST convention — the HTTP method carries the verb) | `GET /api/v1/inventory/products/` (list), `POST /api/v1/inventory/products/` (create) — **not** `/api/v1/inventory/get_products/` |
| Nested resources reflect real ownership | `GET /api/v1/pos/transactions/{id}/items/` |
| Actions that don't map to plain CRUD use a clear verb suffix, DRF `@action` style | `POST /api/v1/kahero/imports/{id}/retry/`, `POST /api/v1/forecasting/forecasts/{id}/regenerate/` |
| Query params `snake_case` | `?branch_id=2&start_date=2026-07-01` |
| JSON body/response keys `snake_case` (matches Python/Django convention rather than switching to camelCase for JSON, since there's no separate JS-framework layer expecting camelCase) | `{"product_id": 14, "quantity_sold": 3}` |

---

## 5. Database Naming

| Element | Convention | Example |
|---|---|---|
| **Tables** (Django auto-generates as `<app>_<model>` — kept as default, not overridden) | lowercase, app-prefixed | `pos_salestransaction`, `inventory_product` |
| **Model class names** | singular, `PascalCase` (Django convention) | `SalesTransaction`, not `SalesTransactions` |
| **Columns/fields** | `snake_case`, singular | `quantity_sold`, `unit_price`, `branch_id` |
| **Foreign keys** | named after the related model, lowercase + `_id` (Django appends `_id` automatically to a FK field named e.g. `branch`) | `branch = models.ForeignKey(Branch, ...)` → column `branch_id` |
| **Boolean fields** | prefixed `is_`/`has_` | `is_active`, `has_low_stock` |
| **Timestamps** | consistent suffix, inherited from `core.models.TimestampedModel` (Phase 4) | `created_at`, `updated_at` |
| **Junction/through tables** | only created explicitly when extra fields are needed (e.g., `SalesItem` linking `SalesTransaction` ↔ `Product` with `quantity`/`subtotal`) — otherwise Django's implicit M2M tables are left as default | — |

---

## 6. Branch Naming *(recap — full detail in Phase 5)*

`feature/<app>-<description>`, `bugfix/<app>-<description>`, `hotfix/<description>`, `chore/<description>` — always lowercase, hyphen-separated description.

## 7. Commit Naming *(recap — full detail in Phase 5)*

Conventional Commits: `<type>(<scope>): <summary>` — e.g., `feat(inventory): add low-stock alert trigger`.

---

## 8. Documentation Format

| Document type | Format/location |
|---|---|
| **Phase planning docs** (this series) | Markdown, stored in `docs/`, one file per phase — already established convention across Phases 1–7. |
| **Code-level documentation** | Docstrings on every service function and non-trivial model method (Google-style, per §1). Inline comments reserved for *why*, not *what* (the code itself should be readable enough to show *what*). |
| **API documentation** | Auto-generated from DRF via `drf-spectacular` (OpenAPI/Swagger schema) — kept in sync with code automatically rather than hand-maintained, reducing drift risk. Exposed at `/api/v1/schema/swagger-ui/` in development only. |
| **README.md** | Project overview, quickstart (matches Phase 6 §7 setup order), links to `docs/` for full phase documentation. |
| **Per-app README** (optional, recommended for `forecasting/` and `kahero_integration/` specifically, since they're the least "standard Django CRUD" apps) | Short `README.md` inside the app folder explaining the non-obvious parts (e.g., how the ARIMA job is scheduled, how a KaHero file moves from upload to ingested). |
| **Commit/PR history** | Serves as the living changelog (Phase 5 §6 versioning) — formal `CHANGELOG.md` generation from tagged releases is optional but easy to add later (`git log v1.0.0..v1.1.0`). |

---

Ready for **Phase 8 — Configuration Files** whenever you'd like to continue.
