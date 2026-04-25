#!/bin/bash
# Backup the admin SQLite database
# Usage: bash scripts/admin_backup.sh [DB_PATH] [BACKUP_DIR]

DB_PATH="${1:-/opt/ai-assistant/data/admin.db}"
BACKUP_DIR="${2:-/opt/ai-assistant/data/backups}"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

BACKUP_FILE="${BACKUP_DIR}/admin_$(date +%F_%H-%M).db"
sqlite3 "$DB_PATH" ".backup '${BACKUP_FILE}'"

if [ $? -eq 0 ]; then
    echo "Backup saved to ${BACKUP_FILE}"
    SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "Size: ${SIZE}"
else
    echo "ERROR: Backup failed"
    exit 1
fi

# Prune backups older than 30 days
PRUNED=$(find "$BACKUP_DIR" -name "admin_*.db" -mtime +30 -delete -print | wc -l)
if [ "$PRUNED" -gt 0 ]; then
    echo "Pruned ${PRUNED} old backup(s)"
fi
