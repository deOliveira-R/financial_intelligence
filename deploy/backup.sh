#!/usr/bin/env bash
# Nightly backup to an rclone remote (e.g. OCI Object Storage or Cloudflare R2). Safe to run
# while the API and syncs run.
#
# Every table except a few can be rebuilt from the raw provider responses
# (`fin-intel rebuild all`), so the backup is:
#   - essential/: the tables that can't be rebuilt (pg_dump, data only): the raw response index
#     (which file is which provider response), the portfolio (accounts, transactions,
#     broker snapshots). Small; kept FI_BACKUP_KEEP_DAYS (default 30).
#   - raw/: the response bodies, content-addressed and immutable (only new files upload).
#   - db/: a full pg_dump, only with FI_BACKUP_DB_SNAPSHOT=1 (fast restore, but tens of GB).
#     Kept FI_BACKUP_DB_KEEP_DAYS (default 8).
# Restore: see deploy/README.md.
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE="${FI_BACKUP_REMOTE:?set FI_BACKUP_REMOTE in .env, e.g. oci:fin-intel-backups}"
KEEP_DAYS="${FI_BACKUP_KEEP_DAYS:-30}"
DB_KEEP_DAYS="${FI_BACKUP_DB_KEEP_DAYS:-8}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DB="${FI_BACKUP_DATABASE:-fin_intel}"  # PostgreSQL database (local socket, this user)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
ESSENTIAL_TABLES=(raw_responses accounts portfolio_transactions position_snapshots)

# Object stores can reject a share of requests transiently (OCI does with new keys); retry
# generously, then verify by checksum so a backup that didn't fully land fails loudly.
RCLONE=(rclone --retries 5 --low-level-retries 20)

# 1. Essential tables first: every index row then has its body among the files copied next.
mkdir "$TMP/essential"
TABLES=()
for table in "${ESSENTIAL_TABLES[@]}"; do TABLES+=(--table "$table"); done
# One transaction-consistent snapshot of all four tables (restore: pg_restore --data-only).
pg_dump --dbname "$DB" --data-only --format custom --compress 9 "${TABLES[@]}" \
  --file "$TMP/essential/essential-$STAMP.dump"
"${RCLONE[@]}" copy "$TMP/essential" "$REMOTE/essential"
"${RCLONE[@]}" check "$TMP/essential" "$REMOTE/essential" --one-way
"${RCLONE[@]}" delete --min-age "${KEEP_DAYS}d" "$REMOTE/essential/"

# 2. Raw bodies. `copy` never deletes remotely: older index dumps may still reference
# responses pruned locally. Syncs keep writing during the backup, so copy and verify the
# files that existed once the index was dumped (bodies are written before their index rows,
# so every dumped row's body is listed); later files go up with the next backup.
(cd data/raw && find . -type f ! -name "*.tmp" | sed 's|^\./||') > "$TMP/raw-files.txt"
"${RCLONE[@]}" copy data/raw "$REMOTE/raw" --files-from "$TMP/raw-files.txt"
"${RCLONE[@]}" check data/raw "$REMOTE/raw" --one-way --files-from "$TMP/raw-files.txt"

# 3. Optional full snapshot (pg_dump is consistent with writers active).
if [ "${FI_BACKUP_DB_SNAPSHOT:-0}" = "1" ]; then
  mkdir "$TMP/db"
  pg_dump --dbname "$DB" --format custom --compress 1 --file "$TMP/db/fin_intel-$STAMP.dump"
  "${RCLONE[@]}" copy "$TMP/db" "$REMOTE/db"
  "${RCLONE[@]}" check "$TMP/db" "$REMOTE/db" --one-way
fi
"${RCLONE[@]}" delete --min-age "${DB_KEEP_DAYS}d" "$REMOTE/db/" 2>/dev/null || true

echo "backup $STAMP done and verified"
