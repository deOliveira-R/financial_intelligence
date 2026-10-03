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

# Object stores can reject a share of requests transiently (OCI does with new keys); retry
# generously, then verify by checksum so a backup that didn't fully land fails loudly.
RCLONE=(rclone --retries 5 --low-level-retries 20)

# .backup takes a transactionally consistent copy, even in WAL mode with writers active.
SNAPSHOT="$TMP/snapshot"
mkdir "$SNAPSHOT"
sqlite3 data/fin_intel.db ".backup '$SNAPSHOT/fin_intel-$STAMP.db'"
gzip -1 "$SNAPSHOT/fin_intel-$STAMP.db"
"${RCLONE[@]}" copy "$SNAPSHOT" "$REMOTE/db"
"${RCLONE[@]}" check "$SNAPSHOT" "$REMOTE/db" --one-way
"${RCLONE[@]}" delete --min-age "${KEEP_DAYS}d" "$REMOTE/db/"

# Raw bodies are content-addressed and immutable, so `copy` only uploads new files. It never
# deletes remotely: older database snapshots may still reference responses pruned locally.
"${RCLONE[@]}" copy data/raw "$REMOTE/raw"
"${RCLONE[@]}" check data/raw "$REMOTE/raw" --one-way

echo "backup $STAMP done and verified"
