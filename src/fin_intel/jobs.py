"""Scheduled steps as states that persist until they succeed.

The schedule only makes steps *due* (`mark_due`); a reconciler (`fin-intel jobs run`, every
few minutes) runs whatever is due or whose retry time has come, one step at a time, and
records how each attempt ended. A failure's kind decides what happens next:

- transient (HTTP 5xx, network, throttling), quota, timeout, interrupted: retried with
  backoff (15 min, 1 h, 4 h, then every 12 h); `retrying`, then `failed` after 4 in a row;
- request (HTTP 4xx), auth (401/403), crash (an exception in our code or data), error (the
  step reported failure): retried after 1 h and 6 h, then daily, `failed` after 2;
- not_configured (a missing key or setting): `blocked`, checked daily.

Nothing is ever given up: `failed` is a state that needs attention while retries go on, so
a fix (deployed code, a provider back online, a key added) turns it `ok` on its own. Every
attempt is a JobEvent with the end of its output, for a person or a model to diagnose.

Steps run after the steps they read (`after`) once those aren't in flight; a step that
succeeds later makes its dependents due again, so a late retry still reaches them.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel.models import JobEvent, JobState
from fin_intel.providers import NotConfiguredError, NotFoundError, ProviderError, QuotaExceededError

OK, DUE, RUNNING, RETRYING = "ok", "due", "running", "retrying"
FAILED, BLOCKED = "failed", "blocked"
TRANSIENT_DELAYS = [
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=4),
    timedelta(hours=12),
]
PERMANENT_DELAYS = [timedelta(hours=1), timedelta(hours=6), timedelta(days=1)]
# kind -> (delays between attempts, consecutive failures before `failed`)
POLICY: dict[str, tuple[list[timedelta], int]] = {
    "transient": (TRANSIENT_DELAYS, 4),
    "quota": (TRANSIENT_DELAYS, 4),
    "timeout": (TRANSIENT_DELAYS, 4),
    "interrupted": (TRANSIENT_DELAYS, 4),
    "request": (PERMANENT_DELAYS, 2),
    "auth": (PERMANENT_DELAYS, 2),
    "crash": (PERMANENT_DELAYS, 2),
    "error": (PERMANENT_DELAYS, 2),
    "not_configured": ([timedelta(days=1)], 1),
}
HINTS = {
    "transient": "The provider was unavailable or throttled; retried automatically.",
    "quota": "The provider's quota was used up; retried after the backoff.",
    "timeout": "The step ran past its time limit; retried. If it keeps happening, the "
    "step needs a longer limit or a smaller batch.",
    "interrupted": "The process stopped before finishing (restart, deploy, out of memory); "
    "retried.",
    "request": "The provider rejected the request (HTTP 4xx): its API may have changed. "
    "Needs a code fix; retried daily meanwhile.",
    "auth": "The provider refused the credentials (HTTP 401/403): check the key in .env.",
    "crash": "An unexpected error in the code or the data: see the output. Needs a fix; "
    "retried daily meanwhile.",
    "error": "The step reported failure without a provider error: see the output.",
    "not_configured": "A required key or setting is missing: set it in .env (see "
    ".env.example). Checked daily.",
}
OUTPUT_TAIL = 8000  # characters of output kept per attempt


@dataclass(frozen=True)
class Step:
    name: str  # stable id, e.g. "market-bars"
    label: str
    group: str  # what marks it due: daily, weekly, news, backup
    run: Callable[[], None]
    after: tuple[str, ...] = ()
    timeout: timedelta = timedelta(hours=3)


@dataclass
class Outcome:
    ok: bool
    kind: str | None = None
    error: str | None = None
    output: str = ""


# Errors the running step met and handled (run_items records each failed item here), so a
# step that exits with failure can say why.
_errors: list[BaseException] = []


def record_error(exc: BaseException) -> None:
    _errors.append(exc)


def take_errors() -> list[BaseException]:
    errors = list(_errors)
    _errors.clear()
    return errors


def kind_of(exc: BaseException) -> str:
    if isinstance(exc, NotConfiguredError):
        return "not_configured"
    if isinstance(exc, QuotaExceededError):
        return "quota"
    if isinstance(exc, ProviderError):
        status = getattr(exc, "status", None)
        if status in (401, 403):
            return "auth"
        if status is not None and 400 <= status < 500 and status not in (408, 429):
            return "request"
        return "transient"
    return "crash"


def classify(errors: Sequence[BaseException]) -> Outcome:
    """How a step that exited with failure failed, from the errors it met. Items that don't
    exist (404s) alone don't fail a step: they stay visible as failing items in sync_state."""
    real = [e for e in errors if not isinstance(e, NotFoundError)]
    if errors and not real:
        return Outcome(ok=True, error=f"{len(errors)} items not found")
    if not real:
        return Outcome(ok=False, kind="error", error="the step reported failure")
    # The most actionable kind first: a missing key or bad credentials explain the rest.
    order = ["not_configured", "auth", "request", "crash", "quota", "transient"]
    kinds = [kind_of(e) for e in real]
    kind = min(kinds, key=order.index)
    first = next(e for e, k in zip(real, kinds, strict=True) if k == kind)
    more = f" (and {len(real) - 1} more errors)" if len(real) > 1 else ""
    return Outcome(ok=False, kind=kind, error=f"{first}"[:1000] + more)


def _states(session: Session) -> dict[str, JobState]:
    return {s.name: s for s in session.scalars(select(JobState))}


def _state(session: Session, name: str, now: datetime) -> JobState:
    state = session.get(JobState, name)
    if state is None:
        state = JobState(name=name, status=OK, attempts=0, updated_at=now)
        session.add(state)
    return state


def mark_due(session: Session, steps: Sequence[Step], group: str, now: datetime) -> int:
    """The schedule's part: every step of the group is to run now. A step already retrying
    is tried now too (keeping its failure count); one running is left alone."""
    count = 0
    for step in steps:
        if step.group != group:
            continue
        state = _state(session, step.name, now)
        if state.status == RUNNING:
            continue
        if state.status == OK:
            state.status = DUE
        state.due_since = state.due_since or now
        state.next_attempt = now
        state.updated_at = now
        count += 1
    session.commit()
    return count


def retry_now(session: Session, name: str, now: datetime) -> None:
    state = _state(session, name, now)
    if state.status != RUNNING:
        state.status = DUE if state.status == OK else state.status
        state.due_since = state.due_since or now
        state.next_attempt = now
        state.updated_at = now
    session.commit()


def recover_interrupted(session: Session, steps: Sequence[Step], now: datetime) -> list[str]:
    """Steps left `running` by a reconciler that died. Called with the reconciler's lock
    held, so nothing else can be running them."""
    by_name = {s.name: s for s in steps}
    names = []
    for state in session.scalars(select(JobState).where(JobState.status == RUNNING)):
        names.append(state.name)
        started = state.started_at or now
        if state.name in by_name:
            outcome = Outcome(ok=False, kind="interrupted", error="the process stopped")
            finish(session, steps, by_name[state.name], outcome, started, now)
        else:
            state.status = OK
    session.commit()
    return names


def next_runnable(
    session: Session, steps: Sequence[Step], now: datetime, skip: set[str] | None = None
) -> Step | None:
    """The first step (in schedule order) that is due or whose retry time has come, and
    none of whose inputs is due or running."""
    states = _states(session)
    waiting = {n for n, s in states.items() if s.status in (DUE, RUNNING)}
    for step in steps:
        if skip and step.name in skip:
            continue
        state = states.get(step.name)
        if state is None or state.status in (OK, RUNNING):
            continue
        if state.next_attempt is not None and state.next_attempt > now:
            continue
        if waiting & set(step.after):
            continue
        return step
    return None


def start(session: Session, step: Step, now: datetime) -> None:
    state = _state(session, step.name, now)
    state.status = RUNNING
    state.started_at = now
    state.updated_at = now
    session.commit()


def finish(
    session: Session,
    steps: Sequence[Step],
    step: Step,
    outcome: Outcome,
    started: datetime,
    now: datetime,
) -> JobState:
    state = _state(session, step.name, now)
    if outcome.ok:
        state.status, state.attempts = OK, 0
        state.due_since = state.next_attempt = None
        state.last_success = now
        state.error_kind = state.hint = None
        state.error = outcome.error  # a note (e.g. items not found), not a failure
        for other in steps:  # dependents read this step's data: run them again
            if step.name in other.after:
                dependent = _state(session, other.name, now)
                if dependent.status == OK:
                    dependent.status, dependent.due_since = DUE, now
                    dependent.next_attempt = now
                    dependent.updated_at = now
    else:
        kind = outcome.kind or "error"
        delays, fail_after = POLICY[kind]
        state.attempts += 1
        state.last_failure = now
        state.error_kind, state.error, state.hint = kind, outcome.error, HINTS[kind]
        state.next_attempt = now + delays[min(state.attempts, len(delays)) - 1]
        if kind == "not_configured":
            state.status = BLOCKED
        else:
            state.status = FAILED if state.attempts >= fail_after else RETRYING
    state.updated_at = now
    session.add(
        JobEvent(
            name=step.name,
            started_at=started,
            finished_at=now,
            status=state.status,
            attempt=state.attempts,
            error_kind=None if outcome.ok else state.error_kind,
            error=outcome.error,
            output=outcome.output[-OUTPUT_TAIL:] or None,
        )
    )
    session.commit()
    return state


@dataclass
class Summary:
    ran: list[tuple[str, str]] = field(default_factory=list)  # (step, resulting status)
    interrupted: list[str] = field(default_factory=list)


def reconcile(
    session: Session,
    steps: Sequence[Step],
    execute: Callable[[Step], Outcome],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Summary:
    """Run runnable steps until none is left. The caller holds the reconciler lock."""
    summary = Summary(interrupted=recover_interrupted(session, steps, clock()))
    tried: set[str] = set()  # once per pass: a retry time that comes meanwhile waits
    while (step := next_runnable(session, steps, clock(), skip=tried)) is not None:
        tried.add(step.name)
        started = clock()
        start(session, step, started)
        outcome = execute(step)
        state = finish(session, steps, step, outcome, started, clock())
        summary.ran.append((step.name, state.status))
    return summary


def overview(
    session: Session, steps: Sequence[Step], problems_only: bool = False
) -> list[dict[str, Any]]:
    """Every step's state, for the API, MCP and a front end: the error and what to do."""
    states = _states(session)
    out = []
    for step in steps:
        s = states.get(step.name)
        status = s.status if s else "never run"
        if problems_only and status in (OK, DUE, RUNNING, "never run"):
            continue
        out.append(
            {
                "name": step.name,
                "label": step.label,
                "group": step.group,
                "after": list(step.after),
                "status": status,
                "attempts": s.attempts if s else 0,
                "last_success": s.last_success if s else None,
                "last_failure": s.last_failure if s else None,
                "next_attempt": s.next_attempt if s and status != OK else None,
                "error_kind": s.error_kind if s else None,
                "error": s.error if s else None,
                "hint": s.hint if s else None,
            }
        )
    return out


def history(session: Session, name: str, limit: int = 10) -> list[dict[str, Any]]:
    """A step's recent attempts, newest first, with the end of each one's output."""
    rows = session.scalars(
        select(JobEvent).where(JobEvent.name == name).order_by(JobEvent.id.desc()).limit(limit)
    )
    return [
        {
            "started_at": e.started_at,
            "finished_at": e.finished_at,
            "status": e.status,
            "attempt": e.attempt,
            "error_kind": e.error_kind,
            "error": e.error,
            "output": e.output,
        }
        for e in rows
    ]
