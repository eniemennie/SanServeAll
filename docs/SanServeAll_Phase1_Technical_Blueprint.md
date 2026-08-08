# SanServeAll — Phase 1 Technical Blueprint
*Extracted from the capstone manuscript for Jorge's Casa De Sans Rival*

---

## 1. Project Overview

**Title:** SanServeAll: A Centralized Web-Based Enterprise Operations and AI-Powered Intelligent Decision Support System for Jorge's Casa De Sans Rival

**Background:**
Jorge's Casa De Sans Rival is a food & beverage business (est. July 19, 2018) with three branches — Batangas City, Alangilan, Lipa City — plus one centralized commissary. Each branch processes ~80–100 transactions/day. Workforce: 7 full-time commissary staff + 2 OJTs, 15+ total across the enterprise. Operations currently run on manual records, spreadsheets, and informal messaging (Viber/Messenger-style tools), causing stock discrepancies, delayed reporting, and no cross-branch visibility.

**Problem Statement (from Table 3-1):**
| # | Problem |
|---|---|
| 1 | Absence of a centralized system — fragmented data across branches/commissary |
| 2 | Delayed data updates — sales/inventory not updated in real time |
| 3 | Inventory inaccuracy and stock risk (over/understock) |
| 4 | Human error in manual encoding |
| 5 | No predictive analytics / automated reporting |
| 6 | Communication inefficiencies between operational units |

**Proposed Solution:**
A centralized web platform unifying POS, inventory management, production/commissary tracking, and an AI-powered decision support layer, with role-based dashboards and ARIMA-based demand forecasting.

**Objectives:**
1. Centralized platform for real-time monitoring of inventory and daily branch operations (reduce manual tasks).
2. Business analytics dashboards visualizing sales trends, inventory movement, and operational performance (ingredients, products, packaging).
3. AI-based predictive models (ARIMA) for demand forecasting to support procurement/replenishment and reduce shortage/overstock/wastage risk.

**Scope:**
- Centralized DB for branch transactions, inventory, and operational data.
- Real-time sync **only** for branches on the native SanServeAll POS (Alangilan, Lipa City).
- **Batangas City branch** (existing **BIR-accredited KaHero POS**, no public API) → **scheduled batch import** (CSV/Excel export from KaHero Back Office → manual upload → parsed/ingested by Django backend, daily or per-shift).

  > ⚠️ Note: the Abstract/Scope narrative in Ch.1 says "Alangilan branch...is utilizing the existing BIR-accredited KaHero POS" and treats Alangilan as the batch-import branch, while Ch.3 (3.2, 3.2.6, System Architecture, Implementation Procedure, Testing) consistently identifies **Batangas City** as the KaHero/batch-import branch and Alangilan as a native-POS, real-time branch. **This is a real inconsistency in the source manuscript that must be clarified with the adviser/client before development** — build against the Chapter 3 version (Batangas City = KaHero/batch) since it recurs in 5+ separate sections, but flag it as an open question rather than silently resolving it.

- Per-batch tracking of ingredient usage (flour, sugar, butter, eggs, milk, flavorings) and packaging (cake boxes, pastry boxes, cups, lids).
- Business analytics dashboards + AI-based predictive/demand-forecasting models.

**Explicit Delimitations / Out of Scope:**
- No customer-facing features: no online ordering, delivery integration, reservations, e-commerce.
- Web-based only — **no offline mode, no native mobile app.**
- No payroll, taxation, banking, or full accounting modules.
- Forecast accuracy is dependent on data quality/seasonality/market factors outside system control — explicitly disclaimed.
- Output quality depends on accurate data encoding by users (garbage-in-garbage-out acknowledged).
- Infrastructure scope limited to standard web hosting + centralized DB + internet infra (no enterprise-grade hardware/network).
- No custom ML model training from scratch (per BSIT capstone scope) — must use pre-built libraries only (statsmodels, scikit-learn, etc.).

---

## 2. Users of the System

### 2.1 Café Branch Owner / Business Owner / Admin
*(Manuscript uses these terms interchangeably in different sections — Ch.3.2.4 = "Business Owner", Use Case Diagram/Ch.3.2.6 = "Café Owner"/"Café Branch Owner", GUI section = "Admin/Manager". Treat as one role: `OWNER_ADMIN`.)*

- **Responsibilities:** system configuration, user account management, RBAC administration, strategic decision-making, monitoring all branches.
- **Permissions:** full access — login/authN admin, user management, system settings/configuration, security settings (2FA), backup/data-retention config, view all analytics/forecasts/reports across branches.
- **Accessible modules:** Admin Dashboard, Analytics Dashboard, Inventory Monitoring, AI-Powered Decision Support Interface, Batch Processing/Batch Management, AI Forecasting Dashboard, AI Resource Management Dashboard, Sales Analytics, Product Performance, Resource Consumption Analytics, Operational Performance, Finished Goods/Materials Tracking, Product Inventory Management, System Settings, System Configuration, Branch Filter (all branches or specific).

### 2.2 Café Branch Staff / Cashier
- **Responsibilities:** process sales transactions (POS), issue receipts, monitor/update stock availability at branch level, start/end shift.
- **Permissions:** branch-scoped only (post branch-selection). Requires **Cashier PIN authentication** in addition to login. Cannot access other branches' data or admin functions.
- **Accessible modules:** Login/Start Screen, Branch Selection, Cashier PIN screen, POS Ordering Screen, Add Custom Product, Order Customization, Personal Customization Settings Panel, Payment Processing, Transaction Receipt.

### 2.3 Commissary Staff
- **Responsibilities:** encode production output, track ingredient usage, update supply/distribution records to branches.
- **Permissions:** production/supply data entry; scoped to commissary functions, not sales or admin config.
- **Accessible modules:** Production recording, Batch Management (implied), supply distribution tracking feeding into Inventory/Analytics.

### 2.4 Customer
- **Not a system actor.** The manuscript explicitly excludes customer-facing functionality (no online ordering/e-commerce/reservations). No customer accounts, logins, or modules exist.

---

## 3. Business Process

### 3.1 Current (Manual) Process — from Fig. 3-2 & §3.2.1
1. Customer arrives at branch → order taken → payment processed manually (cash register / basic POS log).
2. Sale recorded manually per branch.
3. Inventory deducted manually or via periodic spreadsheet update.
4. Stock availability check performed manually.
5. Commissary separately tracks production output, ingredient consumption, batch prep in independent records.
6. Commissary → branches communication via informal messaging platforms, printed reports, or manually sent summaries (no automation).
7. No real-time visibility into sales, inventory, or production status for management.

### 3.2 Problems With Current Process
- Repeated manual encoding/verification/reconciliation → human error.
- Delayed/misinterpreted information transfer between commissary and branches.
- No real-time cross-branch visibility → issues (shortage/overproduction/uneven distribution) detected only after they impact operations.
- No centralized reporting → cannot forecast demand or evaluate performance holistically.

### 3.3 Improved (Proposed) Process
1. Branch staff log in → select branch → (cashier) authenticate via PIN.
2. Sales entered via POS (native SanServeAll for Alangilan & Lipa City = real-time; KaHero branch = logged locally in KaHero, then exported/batch-imported).
3. Inventory auto-deducted on sale (real-time for native-POS branches).
4. Commissary encodes production/ingredient usage directly into system → auto-updates supply/inventory allocation.
5. Centralized DB consolidates all branches + commissary in near-real time (native) / scheduled (KaHero branch).
6. AI module (ARIMA + scikit-learn) runs on schedule (background job) → generates forecasts, risk classifications, natural-language insights (via Claude/OpenAI API).
7. Owner/Admin views consolidated dashboards: sales, inventory, production, forecasting, resource management — filterable by branch.
8. Low-stock/critical alerts and restocking recommendations surfaced automatically.

*(A flowchart/DFD should be rebuilt from Figures 3-2, 3-4, 3-5 during design — the source PDF figures are referenced by caption only, images not extracted in this pass.)*

---

## 4. Functional Requirements (grouped by module)

Derived from Table 3-2 (FR-01…FR-05), the Use Case Diagram description (§3.2.4 actors), and the 28 GUI figure captions (§ GUI, Figures 3-9 to 3-36) — the GUI descriptions are the most granular source of concrete features.

### Authentication & Access
- Login / Start Screen (employee sign-in)
- Start Shift feature (begin daily operations / branch monitoring)
- Branch Selection screen (branch-scoped environment, prevents cross-branch data leakage)
- Cashier PIN Authentication (per-cashier PIN before POS access → transaction accountability)
- Admin Login (email + password, separate from staff login)
- Role-Based Access Control (RBAC) across all modules
- Two-Factor Authentication (2FA) — admin/security config
- Password hashing via PBKDF2

### Point of Sale (POS) — FR-01
- Product catalog browsing with prices, search bar
- Order summary panel
- Add Custom Product (manual name + price entry for off-menu items)
- Order Customization (size, sugar level, add-ons, discounts; e.g., "Spanish Latte" example)
- Cashier UI customization panel (view mode, item view, dark theme, left-handed mode, show order type toggle, receipt-printing toggle)
- Payment Processing (order review, payment mode selection, quick-amount buttons, clear/back/confirm actions, change computation)
- Transaction Receipt generation (products, subtotal, total, payment method, amount paid, change; Print & Done actions)
- Real-time transaction recording for native-POS branches
- Digital receipt auto-generation

### Multi-Branch Inventory Synchronization — FR-02
- Automatic stock deduction across branches on sale
- Low-stock threshold alerts to management
- Real-time inventory monitoring (native POS branches)
- Batch import pipeline for KaHero branch (CSV/Excel upload → parse → ingest, scheduled daily/per-shift)
- Stock order status tracking (processing, in transit, delivered)
- Branch Filter Dropdown (all branches / specific branch)
- Product Inventory Management (name, price, type, stock status available/out-of-stock, quantity, quick-sale action, manual stock adjustment)
- Finished Goods Monitoring (product list, price, stock qty, availability status)
- Materials Tracking (ingredients/materials list, price, availability status, stock qty)
- Batch Management Interface (branch, ingredient/material, quantity, supplier, quality, status; edit/delete actions)
- Stock and Resource Management (per-branch stock levels, status, consumption, estimated days left, item value, supplier info; edit/delete/restore actions)

### Production / Commissary
- Production output encoding
- Ingredient usage tracking
- Supply distribution record updates per branch
- Batch Processing Analytics (weekly batch receiving trends, status breakdown: completed/in progress/pending, total quantity received, overall/category quality pass rates for products/ingredients/beverages/dry goods)
- Operational Performance metrics (avg production time, batch completion rate, resource utilization, quality pass rate, staff efficiency, daily output %)

### Sales Forecasting Module — FR-03
- ARIMA-based time-series forecasting on historical sales data
- Demand forecast generation for inventory/procurement planning

### AI-Powered Intelligent Decision Support — FR-04
- Cross-selling recommendations at POS checkout (native SanServeAll POS)
- Dashboard-based recommendations for KaHero POS branch (since no live checkout hook)
- Inventory risk classification (low/medium/high stock risk) via scikit-learn
- Critical alerts, slow-moving product detection, peak-hour optimization opportunities, unusual pattern detection
- Automated restocking recommendations
- Natural language insight generation (via Claude API / OpenAI API) converting numeric outputs into readable recommendations (stockout warnings, peak demand forecasts, procurement suggestions)
- AI Forecasting Dashboard: model accuracy, data points used, predictions made, average confidence level, trend analysis, 7-day forecast, weekly demand pattern analysis (all branches)
- AI Resource Management Dashboard: inventory tracking, weekly consumption monitoring, category-based pie-chart visualization, AI-driven restocking recommendations

### Role-Specific Dashboards & Reporting — FR-05
- Analytics Dashboard: branch performance, revenue, production output, demand forecast summary, top products, resource monitoring, low-stock alerts
- Sales Analytics Dashboard: weekly sales trend line graphs, total revenue, average daily sales, total units sold
- Product Performance Monitoring: 30-day trend filter, per-product performance & growth-rate analysis
- Resource Consumption Analytics: total resources used, total cost, efficiency, cost-per-product, avg material cost/unit, material utilization
- Consolidated/branch-filtered reporting

### Settings / Configuration (Admin)
- System Settings: notification toggles (lock alerts, sales alerts, staff updates, system updates), display/theme settings (dark mode), Save/Cancel actions
- System Configuration: automatic backups, data retention period, language settings, security settings, 2FA, reset-to-default, Save Changes/Cancel

### Notifications (as surfaced in GUI, not a separate module in the FR table)
- Lock alerts, sales alerts, staff update alerts, system update alerts (toggleable in System Settings)
- Low-stock / critical inventory alerts
- AI-generated critical alerts & unusual pattern detection alerts

---

## 5. Non-Functional Requirements (§3.2.5)

| Category | Requirement (as stated) |
|---|---|
| **Performance** | Handle high volume of concurrent transactions/data requests with minimal delay; rapid report generation under heavy multi-branch load, especially peak hours. |
| **Security** | Encrypted authentication, secure session management, RBAC with clearly defined per-role permissions; PBKDF2 password hashing; protect confidentiality/integrity/availability against internal & external threats. |
| **Reliability** | Minimal downtime/service interruption; error handling, system monitoring, backup processes for fault tolerance/recovery; business continuity for sales/inventory ops. |
| **Usability** | Intuitive layout, clear navigation, simplified workflows for non-technical users; minimal training required. |
| **Scalability** | Support future growth — additional users, branches, functionality — without major structural rework or performance loss. |
| **Data Accuracy/Consistency** | Synchronized DB operations + validation mechanisms across modules so reports/analytics are reliable. |

---

## 6. Database Requirements (§3.4, Fig. 3-7 ERD — narrative only, no visual extracted)

**Entities identified in the narrative:**

| Entity | Notes |
|---|---|
| `USER` | Central entity for auth/identification; linked to `ROLE` |
| `ROLE` | Governs permissions/access level (RBAC) |
| `SALES_TRANSACTION` | Linked to `USER` (who processed it) and `PRODUCT`; captures real-time sales |
| `SALES_ITEM` | Line items of a transaction: qty, unit price, subtotal, total — linked to `SALES_TRANSACTION` and `PRODUCT` |
| `PRODUCT` | Linked to `INVENTORY`, `INVENTORY_TRANSACTION`, `SALES_ITEM` |
| `INVENTORY` | Current stock levels per product, reorder thresholds |
| `INVENTORY_TRANSACTION` | Logs every stock movement (in/out) for traceability |
| `ANALYTICS_DATA` | Consolidated historical sales/product/inventory dataset — feeds `FORECAST` |
| `FORECAST` | Predictive output (demand forecasts) generated from `ANALYTICS_DATA` |

**Implied but not explicitly named as tables (must be designed in Phase 4):**
- `BRANCH` (Batangas City / Alangilan / Lipa City — referenced constantly but no explicit ERD entity named)
- `COMMISSARY` / `PRODUCTION_RECORD` (production module — Batch Management fields: branch, ingredient/material, quantity, supplier, quality, status)
- `BATCH` (batch processing/batch management — status: completed/in progress/pending)
- Session/PIN auth artifacts for cashier PIN flow
- KaHero batch-import staging table (raw CSV/Excel imports before ingestion into `SALES_TRANSACTION`/`INVENTORY_TRANSACTION`)

**Design principles stated:**
- Proper normalization to minimize redundancy.
- Referential integrity via primary/foreign keys across all relationships.
- Must support scalability (future modules without disrupting existing schema).
- Production DB = MySQL (relational); SQLite reserved for dev/prototyping only (Table 3-3).

---

## 7. System Architecture (§3.3, Fig. 3-6)

Three-layer architecture:

**Presentation Layer (Frontend):**
- KaHero POS system (Batangas City branch, per Ch.3 — external, not developed by this team)
- SanServeAll native POS interface (Alangilan & Lipa City branches, per Ch.3)
- Branch Owner interface
- Admin Dashboard
- Built with HTML5, CSS3, JavaScript, Bootstrap 5

**Application Layer (Backend):**
- Django (Python) framework — API communication, middleware, business rules, data transformation
- Real-time processing path for native-POS branches
- Batch integration path for KaHero branch (periodic import → validate → sync)
- Inventory logic: stock deduction, replenishment tracking, discrepancy validation
- Production coordination (commissary output ↔ branch demand)
- AI/ML execution: ARIMA forecasting, scikit-learn classification — run as scheduled background jobs (async), decoupled from live POS to avoid performance impact
- Generates: analytics, alerts, forecasts, consolidated reports → passed to frontend

**Data Layer (Database):**
- MySQL relational DB — sales transactions, inventory records, production logs, branch profiles, user accounts, stock movement history, AI forecasting datasets
- Normalized relational schema, PK/FK constraints
- Serves both real-time and batch-synced data consistently

**Cross-cutting:**
- Background/async scheduled jobs for forecasting & large-scale aggregation (do not block real-time POS)
- Designed for horizontal scalability (add branches/modules without structural rework)

---

## 8. UI Screens (Figures 3-9 through 3-36 — 28 screens total)

| # | Screen | Primary Role |
|---|---|---|
| 3-9 | Login / Start Screen (+ Start Shift) | Staff |
| 3-10 | Branch Selection Interface | Staff |
| 3-11 | Cashier PIN Authentication Screen | Cashier |
| 3-12 | POS Ordering Screen | Cashier |
| 3-13 | Add Custom Product Interface | Cashier |
| 3-14 | Order Customization Interface | Cashier |
| 3-15 | Branch Staff Customization Settings Panel | Cashier |
| 3-16 | POS Payment Processing Interface | Cashier |
| 3-17 | Transaction Receipt Interface | Cashier |
| 3-18 | Admin Login Interface | Owner/Admin |
| 3-19 | Analytics Dashboard Interface | Owner/Admin |
| 3-20 | Inventory Monitoring Interface | Owner/Admin |
| 3-21 | AI-Powered Decision Support Interface | Owner/Admin |
| 3-22 | Branch Filter Dropdown | Owner/Admin |
| 3-23 | Batch Processing Analytics Dashboard | Owner/Admin, Commissary |
| 3-24 | Batch Management Interface | Commissary |
| 3-25 | AI-Powered Forecasting Dashboard | Owner/Admin |
| 3-26 | AI-Powered Resource Management Dashboard | Owner/Admin |
| 3-27 | Stock and Resource Management Interface | Owner/Admin |
| 3-28 | Sales Analytics Dashboard | Owner/Admin |
| 3-29 | Product Performance Monitoring Interface | Owner/Admin |
| 3-30 | Resource Consumption Analytics Interface | Owner/Admin |
| 3-31 | Operational Performance Interface | Owner/Admin |
| 3-32 | Finished Goods Monitoring Interface | Owner/Admin |
| 3-33 | Materials Tracking Interface | Owner/Admin |
| 3-34 | Product Inventory Management Interface | Owner/Admin |
| 3-35 | Admin's System Settings Interface | Owner/Admin |
| 3-36 | Admin's System Configuration Interface | Owner/Admin |

*(Actual figure images referenced as `[imageN]` were not included/extractable from the uploaded manuscript text — only captions and body-text descriptions were available. Recommend pulling the original image assets from the source document/DOCX before wireframing in Phase 6.)*

---

## 9. Reports

- Sales reports (per branch / consolidated)
- Inventory reports (stock levels, movement history)
- Production summaries (commissary output, ingredient usage)
- AI Forecast reports (demand forecast, 7-day forecast, weekly demand pattern, confidence levels, model accuracy)
- Inventory risk classification report (low/medium/high)
- Resource consumption / cost analysis reports
- Product performance & growth-rate reports
- Operational performance report (production time, batch completion rate, staff efficiency, daily output %)
- Batch processing/quality reports (per category: products, ingredients, beverages, dry goods)
- UAT / Likert-scale evaluation reports (weighted mean, standard deviation) — for the study itself, not a runtime system feature

---

## 10. Notifications

- Low-stock / critical stock alerts (Inventory Monitoring, AI Decision Support)
- Lock alerts (System Settings toggle)
- Sales alerts (System Settings toggle)
- Staff update alerts (System Settings toggle)
- System update alerts (System Settings toggle)
- AI-generated critical alerts / unusual pattern detection alerts
- Batch/order status notifications (processing, in transit, delivered)

*(The manuscript does not specify delivery channel — e.g., in-app banner vs. email vs. SMS — this is a decision to raise with the client in Phase 3+ requirements confirmation.)*

---

## 11. Business Rules (extracted)

1. Real-time synchronization applies **only** to branches using the SanServeAll native POS module.
2. The KaHero-POS branch (Batangas City per Ch.3 body text; Alangilan per Ch.1 abstract/scope — **conflict, see §1 note above**) uses **scheduled batch import** (daily or per-shift) via CSV/Excel export from KaHero Back Office; no live API integration is possible (KaHero has no public/third-party API).
3. Every cashier must authenticate via a unique PIN before performing POS actions (accountability/traceability of every transaction to an individual).
4. Every sales transaction is linked to the user (cashier) who processed it, and is broken into line items in `SALES_ITEM` for auditability.
5. Inventory is auto-deducted whenever a sale is recorded (native POS branches, real time); predefined reorder thresholds trigger low-stock alerts.
6. AI/ML processing (forecasting, classification) must run as scheduled background jobs, decoupled from live POS transaction processing, so it never degrades real-time performance.
7. AI outputs (forecasts, risk classifications) are stored in a dedicated analytics table for fast retrieval — not recomputed on every dashboard load.
8. The AI/DSS functions strictly as a **decision-support** tool, not an autonomous decision-maker — outputs must be presented as recommendations for human (owner/admin) judgment, avoiding manipulative or biased recommendations.
9. RBAC enforced everywhere: each role (branch staff, commissary staff, management/owner, system admin) has clearly scoped permissions; Branch Selection prevents cross-branch data bleed.
10. Passwords hashed with PBKDF2; admin accounts additionally support 2FA.
11. Data privacy: compliance with the Philippine Data Privacy Act of 2012 (RA 10173) for all PII (user credentials, access logs, operational records).
12. Custom ML model development from scratch is out of scope (BSIT capstone constraint) — only pre-built, well-established Python libraries (statsmodels, pandas, numpy, scikit-learn) may be used for AI features.
13. Development methodology is strictly Agile (iterative cycles: Requirement Analysis → Design → Implementation → Testing → Deployment → Maintenance → Launch), with mandatory adviser validation gates between phases.
14. System is web-only — no offline mode, no native mobile app is in scope.

---

## Open Questions to Resolve Before Coding (flag to adviser/client)

1. **Alangilan vs. Batangas City** — which branch actually runs KaHero POS/batch-import vs. native POS/real-time? The Abstract and §1 (Background, Objectives, Significance) say **Alangilan**; §3.2, §3.2.6, System Architecture, Implementation Procedure (Stage 2), and Testing (§3.8) all say **Batangas City**. This directly affects POS assignment logic and must be confirmed with the actual client (Jorge's Café) before building branch configuration.
2. Exact notification delivery channel(s) — in-app only, or email/SMS also expected?
3. Whether "Business Owner," "Café Owner," "Café Branch Owner," and "Admin/Manager" are meant to be one single role or if Admin (system config) and Owner (business analytics) should be split into two distinct accounts/permission sets — the manuscript uses the terms inconsistently across sections.
4. Confirm whether commissary staff need their own login/role in the DB or are managed as a sub-type of branch staff.
5. Data source and format for historical sales data to seed/train the ARIMA model (minimum data volume, granularity — daily/weekly/monthly).

---

*This blueprint completes Phase 1 only, per the project instructions. Awaiting confirmation (and resolution of the open questions above, especially #1) before proceeding to Phase 2 (technology stack recommendation).*
