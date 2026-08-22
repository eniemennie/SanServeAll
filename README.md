# SanServeAll: A Centralized Web-Based Enterprise Operations and AI-Powered Intelligent Decision Support System for Jorge’s Casa De Sans Rival

Centralized Web-Based Enterprise Operations and AI-Powered Intelligent
Decision Support System for Jorge's Casa De Sans Rival.

## Quickstart (local development)

```bash
git clone <repo-url>
cd sanserveall
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r backend/requirements/development.txt
cp .env.example .env              # fill in local values
cd backend
python manage.py migrate
python manage.py runserver
```

## Project Documentation

Full planning documentation (architecture, tech stack, standards, timeline,
etc.) lives in [`docs/`](docs/) — Phases 1 through 10, plus standalone notes.

## Branching

`feature/*` / `bugfix/*` branch off `staging`. `staging` -> `main` via PR
once verified (`main` = production). See `docs/SanServeAll_Phase5_GitHub_Strategy.md`.

## Team

- J. Aguila — Documentation
- A. Banaag — Technical (POS, Inventory, Production, front-end-facing modules)
- D. Catapang — Technical (Auth/RBAC, KaHero integration, Analytics, AI/Forecasting)
