# Backup Cron Setup (PythonAnywhere Scheduled Tasks)

Add a daily scheduled task (PythonAnywhere dashboard -> Tasks) that runs:

    bash /home/<youruser>/sanserveall/backend/scripts/backup_media_and_db.sh

Verify with an actual restore drill before go-live (Phase 10 checklist) —
do not treat "the script exits 0" as sufficient proof of a working backup.
