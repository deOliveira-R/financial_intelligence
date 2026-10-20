#!/bin/sh
# Entrypoint of the cron service: writes the crontab from the FI_SCHEDULE_* settings
# (defaults in compose.yaml, overrides in .env; an empty schedule turns the job off), then
# runs it with supercronic (cron for containers: logs to stdout, never overlaps a job that
# is still running). Times are UTC. Syncs pick up from their last success, so a run missed
# while the server was down is covered by the next one.
set -eu
tab=/tmp/crontab
: > "$tab"
job() {
  if [ -n "$1" ]; then echo "$1 $2" >> "$tab"; fi
}
job "${FI_SCHEDULE_DAILY:-}" "nice -n 10 fin-intel sync-daily"
job "${FI_SCHEDULE_WEEKLY:-}" "nice -n 10 fin-intel sync-weekly"
job "${FI_SCHEDULE_NEWS:-}" "fin-intel sync-news --stalest 1"
job "${FI_SCHEDULE_BACKUP:-}" "nice -n 15 /app/deploy/backup.sh"
cat "$tab"
exec supercronic -passthrough-logs "$tab"
