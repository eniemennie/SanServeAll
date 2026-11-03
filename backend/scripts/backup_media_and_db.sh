#!/usr/bin/env bash
# Nightly backup: media/ folder + the live SQLite database. Referenced by
# Phase 3's durability decision.
#
# The deployment uses SQLite, not MySQL (see config/settings/production.py
# and config/settings/staging.py for the full reasoning -- PythonAnywhere's
# free tier stopped including a managed database for accounts created from
# 2026-01-15 onward). This script originally shipped written against
# MySQL/mysqldump from an earlier plan and was never updated after that
# switch -- on the real deployment it would tar media/ successfully and
# then fail on the mysqldump line every night (no MySQL server to connect
# to), silently producing a media-only backup with no database dump unless
# someone actually read the cron log. Fixed here to back up the real
# database engine that's actually running.
#
# Uses `sqlite3 <path> ".backup ..."` rather than a plain `cp`/`tar` of the
# .sqlite3 file -- the deployment runs in WAL mode (see the settings files),
# so a raw file copy can grab the main db file mid-write, without the
# matching -wal/-shm data, and produce a corrupt or inconsistent snapshot.
# The sqlite3 .backup command is safe to run against a live database.
#
# Configure via environment before running (e.g. in the PythonAnywhere
# Scheduled Task's command, or a sourced env file) -- do not hardcode paths
# or credentials in this file:
#   SQLITE_DB_PATH   Same variable Django itself reads (config/settings/
#                     production.py / staging.py). Defaults below to the
#                     production path, since the team's one live app
#                     actually runs config.settings.production (confirmed
#                     2026-11-03 from its real WSGI file and .env -- see
#                     deployment/production/notes.md) -- override if
#                     backing up a different environment.
set -e

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$SCRIPT_DIR/../../database/backups"
mkdir -p "$BACKUP_DIR"

SQLITE_DB_PATH="${SQLITE_DB_PATH:-$HOME/data/sanserveall_production.sqlite3}"

if [ ! -f "$SQLITE_DB_PATH" ]; then
  echo "ERROR: SQLite database not found at $SQLITE_DB_PATH" >&2
  echo "Set SQLITE_DB_PATH to the live database file (same value Django's" >&2
  echo "SQLITE_DB_PATH setting uses) before running this script." >&2
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "ERROR: sqlite3 CLI not found on PATH -- cannot take a safe live backup." >&2
  exit 1
fi

# Found via an actual restore drill against staging (the deployment notes'
# own warning not to trust "the script exits 0" without one): MEDIA_ROOT
# (config/settings/base.py) is never created by Django itself -- it only
# comes into existence the first time something is actually saved there.
# A freshly-deployed environment with no uploads yet has no media/
# directory at all, so `tar` failed outright here, and `set -e` aborted
# the whole script before it ever reached the database backup below --
# silently skipping the one part of this backup that actually matters.
# Creating it first makes an empty media/ back up as a (harmless) empty
# archive instead of taking the whole nightly backup down with it.
mkdir -p "$SCRIPT_DIR/../media/"
tar -czf "$BACKUP_DIR/media_$TIMESTAMP.tar.gz" "$SCRIPT_DIR/../media/"

sqlite3 "$SQLITE_DB_PATH" ".backup '$BACKUP_DIR/db_$TIMESTAMP.sqlite3'"

echo "Backup complete: $BACKUP_DIR/media_$TIMESTAMP.tar.gz, $BACKUP_DIR/db_$TIMESTAMP.sqlite3"
# TODO: add a step here to copy these two files to an off-server destination —
# a backup that only lives on the same disk as the live system doesn't
# protect against disk failure.
