#!/usr/bin/env bash
# Local CI-equivalent check, mirroring .github/workflows/ci.yml exactly
# (flake8 . from backend/, black --check backend from repo root, pytest).
# Run this before every commit/PR -- if it's clean here, CI won't surprise you.
#
# Usage (from backend/, with the venv active):
#   ./scripts/verify.sh
set -e

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(dirname "$BACKEND_DIR")"

echo "== migrate (dev SQLite) =="
cd "$BACKEND_DIR"
python manage.py migrate

echo "== flake8 . =="
flake8 .

echo "== black --check backend =="
cd "$ROOT_DIR"
black --check backend

echo "== pytest =="
cd "$BACKEND_DIR"
pytest

echo -e "\nAll checks passed -- matches CI."
