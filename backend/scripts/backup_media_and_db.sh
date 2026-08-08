#!/usr/bin/env bash
# Nightly backup: media/ folder + MySQL dump. Referenced by Phase 3's
# durability decision. Configure DB_NAME/DB_USER/DB_PASSWORD via environment
# before running (do not hardcode credentials in this file).
set -e

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="../../database/backups"
mkdir -p "$BACKUP_DIR"

tar -czf "$BACKUP_DIR/media_$TIMESTAMP.tar.gz" ../media/

mysqldump -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" \
  > "$BACKUP_DIR/db_$TIMESTAMP.sql"

echo "Backup complete: $BACKUP_DIR/media_$TIMESTAMP.tar.gz, $BACKUP_DIR/db_$TIMESTAMP.sql"
# TODO: add a step here to copy these two files to an off-server destination —
# a backup that only lives on the same disk as the live system doesn't
# protect against disk failure.
