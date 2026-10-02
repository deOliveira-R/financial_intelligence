#!/usr/bin/env bash
# Nightly backup: a consistent SQLite snapshot plus new raw response files, to an rclone
# remote (e.g. Cloudflare R2 or OCI Object Storage). Safe to run while the API and syncs run.
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE="${FI_BACKUP_REMOTE:?set FI_BACKUP_REMOTE in .env, e.g. r2:fin-intel-backups}"
KEEP_DAYS="${FI_BACKUP_KEEP_DAYS:-8}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# .backup takes a transactionally consistent copy, even in WAL mode with writers active.
sqlite3 data/fin_intel.db ".backup '$TMP/fin_intel.db'"
gzip -1 "$TMP/fin_intel.db"
rclone copyto "$TMP/fin_intel.db.gz" "$REMOTE/db/fin_intel-$STAMP.db.gz"
rclone delete --min-age "${KEEP_DAYS}d" "$REMOTE/db/"

# Raw bodies are content-addressed and immutable, so `copy` only uploads new files. It never
# deletes remotely: older database snapshots may still reference responses pruned locally.
rclone copy data/raw "$REMOTE/raw"

echo "backup $STAMP done"
