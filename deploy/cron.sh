#!/bin/sh
# Entrypoint of the cron service: writes the crontab from the FI_SCHEDULE_* settings
# (defaults in compose.yaml, overrides in .env; an empty schedule turns the job off), then
# runs it with supercronic (cron for containers: logs to stdout, never overlaps a job that
# is still running). Times are UTC.
#
# The schedule only marks groups of steps due; the reconciler (`jobs run`, every few
# minutes) runs them and retries failures with backoff until they succeed (jobs.py).
set -eu
tab=/tmp/crontab
: > "$tab"
job() {
  if [ -n "$1" ]; then echo "$1 $2" >> "$tab"; fi
}
job "${FI_SCHEDULE_DAILY:-}" "fin-intel jobs due daily"
job "${FI_SCHEDULE_WEEKLY:-}" "fin-intel jobs due weekly"
job "${FI_SCHEDULE_NEWS:-}" "fin-intel jobs due news"
job "${FI_SCHEDULE_BACKUP:-}" "fin-intel jobs due backup"
job "${FI_SCHEDULE_RECONCILE:-}" "nice -n 10 fin-intel jobs run"
cat "$tab"
exec supercronic -passthrough-logs "$tab"
