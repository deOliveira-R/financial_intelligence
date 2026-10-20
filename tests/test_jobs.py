from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from fin_intel import jobs
from fin_intel.models import JobEvent, JobState
from fin_intel.providers import (
    NotConfiguredError,
    NotFoundError,
    ProviderError,
    QuotaExceededError,
)

T0 = datetime(2026, 10, 20, 23, 30, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def step(name, group="daily", after=()):
    return jobs.Step(name, name, group, lambda: None, after=after)


def state(session, name) -> JobState:
    session.expire_all()
    return session.get(JobState, name)


def test_a_failing_step_is_retried_with_backoff_until_it_succeeds(session):
    steps = [step("bars"), step("fred")]
    clock = Clock()
    results = {"bars": [], "fred": []}
    outcomes = {
        "bars": [jobs.Outcome(ok=False, kind="transient", error="HTTP 503", output="...503")] * 4
        + [jobs.Outcome(ok=True)],
        "fred": [jobs.Outcome(ok=True)],
    }

    def execute(s):
        results[s.name].append(clock.now)
        return outcomes[s.name].pop(0)

    jobs.mark_due(session, steps, "daily", clock.now)
    summary = jobs.reconcile(session, steps, execute, clock)
    # One failure doesn't stop the others.
    assert summary.ran == [("bars", "retrying"), ("fred", "ok")]
    bars = state(session, "bars")
    assert (bars.attempts, bars.error_kind, bars.next_attempt) == (
        1,
        "transient",
        T0 + timedelta(minutes=15),
    )
    assert "retried automatically" in bars.hint

    # Nothing runs before the retry time.
    clock.now = T0 + timedelta(minutes=10)
    assert jobs.reconcile(session, steps, execute, clock).ran == []
    # Then backoff: 15 min, 1 h, 4 h; failed after 4 in a row, still retried (12 h).
    for wait in (timedelta(hours=1), timedelta(hours=4), timedelta(hours=12)):
        clock.now = state(session, "bars").next_attempt
        jobs.reconcile(session, steps, execute, clock)
        assert state(session, "bars").next_attempt == clock.now + wait
    bars = state(session, "bars")
    assert (bars.status, bars.attempts) == ("failed", 4)
    clock.now = bars.next_attempt
    assert jobs.reconcile(session, steps, execute, clock).ran == [("bars", "ok")]
    bars = state(session, "bars")
    assert (bars.status, bars.attempts, bars.error, bars.last_success) == (
        "ok",
        0,
        None,
        clock.now,
    )
    events = session.scalars(select(JobEvent).where(JobEvent.name == "bars")).all()
    assert [e.status for e in events] == ["retrying", "retrying", "retrying", "failed", "ok"]
    assert events[0].output == "...503"


def test_kinds_set_the_policy(session):
    steps = [step("a"), step("b"), step("c")]
    clock = Clock()
    outcomes = {
        "a": jobs.Outcome(ok=False, kind="not_configured", error="FI_X is not set"),
        "b": jobs.Outcome(ok=False, kind="crash", error="KeyError: 'x'"),
        "c": jobs.Outcome(ok=False, kind="auth", error="HTTP 403"),
    }
    jobs.mark_due(session, steps, "daily", clock.now)
    jobs.reconcile(session, steps, lambda s: outcomes[s.name], clock)
    assert state(session, "a").status == "blocked"
    assert state(session, "a").next_attempt == T0 + timedelta(days=1)
    assert state(session, "b").status == "retrying"
    assert state(session, "b").next_attempt == T0 + timedelta(hours=1)
    clock.now = T0 + timedelta(hours=1)
    jobs.reconcile(session, steps, lambda s: outcomes[s.name], clock)
    # Permanent kinds fail after two and are then retried daily.
    assert state(session, "b").status == "failed"
    assert state(session, "b").next_attempt == clock.now + timedelta(hours=6)
    assert "check the key" in state(session, "c").hint


def test_dependents_wait_for_inputs_in_flight_and_rerun_after_a_late_success(session):
    steps = [step("bars"), step("fred"), step("metrics", after=("bars", "fred"))]
    clock = Clock()
    order = []
    fail_bars = [True]

    def execute(s):
        order.append(s.name)
        if s.name == "bars" and fail_bars[0]:
            return jobs.Outcome(ok=False, kind="transient", error="timeout")
        return jobs.Outcome(ok=True)

    # metrics comes after its inputs even when listed first... and doesn't wait for a
    # failed input forever.
    jobs.mark_due(session, steps, "daily", clock.now)
    jobs.reconcile(session, steps, execute, clock)
    assert order == ["bars", "fred", "metrics"]
    # bars succeeds on retry: metrics runs again with the new data.
    fail_bars[0] = False
    clock.now = T0 + timedelta(minutes=15)
    jobs.reconcile(session, steps, execute, clock)
    assert order[3:] == ["bars", "metrics"]
    assert state(session, "metrics").status == "ok"


def test_an_interrupted_run_is_recovered_and_retried(session):
    steps = [step("bars")]
    clock = Clock()
    jobs.mark_due(session, steps, "daily", clock.now)
    jobs.start(session, steps[0], clock.now)  # then the reconciler dies
    clock.now = T0 + timedelta(minutes=5)
    summary = jobs.reconcile(session, steps, lambda s: jobs.Outcome(ok=True), clock)
    assert summary.interrupted == ["bars"]
    bars = state(session, "bars")
    assert (bars.error_kind, bars.status) == ("interrupted", "retrying")
    clock.now = bars.next_attempt
    assert jobs.reconcile(session, steps, lambda s: jobs.Outcome(ok=True), clock).ran == [
        ("bars", "ok")
    ]


def test_marking_due_keeps_failure_counts_and_skips_other_groups(session):
    steps = [step("bars"), step("tickers", group="weekly")]
    clock = Clock()
    jobs.mark_due(session, steps, "daily", clock.now)
    jobs.reconcile(
        session, steps, lambda s: jobs.Outcome(ok=False, kind="transient", error="x"), clock
    )
    clock.now = T0 + timedelta(minutes=5)
    assert jobs.mark_due(session, steps, "daily", clock.now) == 1
    bars = state(session, "bars")
    assert (bars.status, bars.attempts, bars.next_attempt) == ("retrying", 1, clock.now)
    assert state(session, "tickers") is None


def test_classify_errors():
    assert jobs.classify([NotFoundError("404"), NotFoundError("404")]).ok
    assert jobs.classify([]).kind == "error"
    assert jobs.classify([ProviderError("HTTP 503", 503)]).kind == "transient"
    assert jobs.classify([ProviderError("connect timeout")]).kind == "transient"
    assert jobs.classify([ProviderError("HTTP 401", 401)]).kind == "auth"
    assert jobs.classify([ProviderError("HTTP 400", 400)]).kind == "request"
    assert jobs.classify([QuotaExceededError("429")]).kind == "quota"
    assert jobs.classify([KeyError("x")]).kind == "crash"
    mixed = jobs.classify([ProviderError("HTTP 503", 503), NotConfiguredError("FI_KEY unset")])
    assert (mixed.kind, mixed.error) == ("not_configured", "FI_KEY unset (and 1 more errors)")
