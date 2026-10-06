"""Yen carry trade gauge: how attractive the trade is, how crowded, and signs of an unwind.

Borrowing in yen at near-zero rates to hold higher-yielding assets (US Treasuries, Mexican
peso, US tech) pays the rate differential as long as the yen doesn't rally. When it does,
positions unwind together: in August 2024 USD/JPY fell about 12% in a month, the Nikkei
12% in a day, and the selling spread to US equities. The gauge reports:

- **Attractiveness:** US-Japan yield differentials (3-month, 2-year, 10-year) and
  carry-to-risk: the 3-month differential per unit of USD/JPY volatility.
- **Crowding:** leveraged funds' yen futures positioning (CFTC) and its 3-year COT index;
  speculators deeply short the yen means a crowded carry trade.
- **Funding pressure:** Japanese residents' weekly purchases of foreign bonds (MoF);
  sustained net selling is repatriation.
- **Triggers:** BOJ and FOMC meetings ahead, a fast yen rally, a volatility spike, a
  narrowing differential.

Every figure is point in time (what was known on `as_of`), with its percentile over the
previous three years. Flags describe conditions worth attention; they aren't trade signals
and their thresholds haven't been fitted to outcomes.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import indicators, japan, releases, timeseries
from fin_intel.models import EconomicObservation, EconomicReleaseDate

SPECS = {
    "usdjpy": "fred:DEXJPUS",
    "us3m": "fred:DGS3MO",
    "jp3m": "fred:IR3TIB01JPM156N",
    "us2y": "fred:DGS2",
    "jp2y": "jp:jgb2y",
    "us10y": "fred:DGS10",
    "jp10y": "jp:jgb10y",
    "mxnusd": "fred:DEXMXUS",
    "audusd": "fred:DEXUSAL",
    "vix": "fred:VIXCLS",
    "yen_spec_net": "cot:yen:leveraged:net",
    "yen_spec_index": "cot:yen:leveraged:index",
}
LOOKBACK = 756  # trading days in the percentile window (three years)
MONTH, QUARTER = 21, 63  # trading days
FLOW_WEEKS = 4

# Flag thresholds: judgment calls, to be revisited in the signal-design work.
YEN_RALLY = -0.05  # USD/JPY down 5% or more in a month
HIGH_PERCENTILE = 0.9  # volatility or VIX in its top decile
CROWDED_INDEX = 10.0  # leveraged funds' yen positioning in its bottom decile (short yen)
NARROWING = -0.5  # 2-year differential down half a point or more in three months
LOW_PERCENTILE = 0.1  # repatriation: 4-week foreign bond purchases in their bottom decile
MEETING_SOON = timedelta(days=7)


@dataclass
class Reading:
    name: str
    value: float | None
    change_1m: float | None = None  # absolute, or relative for rates of exchange
    percentile_3y: float | None = None


@dataclass
class Gauge:
    as_of: date
    readings: list[Reading]
    flags: list[str] = field(default_factory=list)
    next_boj: date | None = None
    next_fomc: date | None = None


def _percentile(series: list[float | None], i: int) -> float | None:
    current = series[i]
    window = [v for v in series[max(0, i - LOOKBACK) : i + 1] if v is not None]
    if current is None or len(window) < LOOKBACK // 3:
        return None
    return sum(v <= current for v in window) / len(window)


def _at(series: list[float | None], i: int) -> float | None:
    return series[i] if 0 <= i < len(series) else None


def _change(series: list[float | None], i: int, n: int, relative: bool) -> float | None:
    now, then = _at(series, i), _at(series, i - n)
    if now is None or then is None:
        return None
    return now / then - 1 if relative else now - then


def _combine(a, b, fn) -> list[float | None]:
    return [None if x is None or y is None else fn(x, y) for x, y in zip(a, b, strict=True)]


def _load(session: Session, end: date | None) -> tuple[list[date], dict[str, list]]:
    """Each input on the trading calendar; inputs not loaded yet are all None."""
    days, out = None, {}
    for name, spec in SPECS.items():
        try:
            days, series = timeseries.build(session, [spec], end=end, pit=True)
            out[name] = series[spec]
        except timeseries.SpecError:
            out[name] = None
    if days is None:
        raise timeseries.SpecError("no carry inputs loaded; run sync-economic and sync-japan")
    return days, {k: v if v is not None else [None] * len(days) for k, v in out.items()}


def _flows(session: Session, as_of: date) -> tuple[float | None, float | None]:
    """Residents' net foreign bond purchases over the last FLOW_WEEKS published weeks, and
    that sum's percentile among rolling sums over three years."""
    rows = session.execute(
        select(EconomicObservation.date, EconomicObservation.value)
        .where(EconomicObservation.series_id == "MOF_OUT_BONDS")
        .order_by(EconomicObservation.date)
    ).all()
    known = [v for d, v in rows if japan.available_on("MOF_OUT_BONDS", d) <= as_of]
    sums = [sum(known[i - FLOW_WEEKS + 1 : i + 1]) for i in range(FLOW_WEEKS - 1, len(known))]
    if not sums:
        return None, None
    window = sums[-156:]  # three years of weeks
    return sums[-1], sum(v <= sums[-1] for v in window) / len(window)


def _next_meeting(session: Session, release_id: int, after: date) -> date | None:
    return session.scalar(
        select(EconomicReleaseDate.date)
        .where(EconomicReleaseDate.release_id == release_id, EconomicReleaseDate.date >= after)
        .order_by(EconomicReleaseDate.date)
        .limit(1)
    )


def gauge(session: Session, as_of: date | None = None) -> Gauge:
    days, s = _load(session, as_of)
    i = len(days) - 1
    day = days[i]
    diff_3m = _combine(s["us3m"], s["jp3m"], lambda a, b: a - b)
    diff_2y = _combine(s["us2y"], s["jp2y"], lambda a, b: a - b)
    diff_10y = _combine(s["us10y"], s["jp10y"], lambda a, b: a - b)
    vol = [None if v is None else v * 100 for v in indicators.volatility(s["usdjpy"], QUARTER)]
    carry_to_risk = _combine(diff_3m, vol, lambda d, v: d / v if v else None)
    mxnjpy = _combine(s["usdjpy"], s["mxnusd"], lambda jpy, mxn: jpy / mxn)
    audjpy = _combine(s["usdjpy"], s["audusd"], lambda jpy, aud: jpy * aud)

    def reading(name: str, series: list, relative: bool = False) -> Reading:
        return Reading(
            name, _at(series, i), _change(series, i, MONTH, relative), _percentile(series, i)
        )

    readings = [
        reading("usd_jpy", s["usdjpy"], relative=True),
        reading("usd_jpy_vol_3m_pct", vol),
        reading("diff_3m_pct", diff_3m),
        reading("diff_2y_pct", diff_2y),
        reading("diff_10y_pct", diff_10y),
        reading("carry_to_risk", carry_to_risk),
        reading("mxn_jpy", mxnjpy, relative=True),
        reading("aud_jpy", audjpy, relative=True),
        reading("jgb_10y_pct", s["jp10y"]),
        reading("vix", s["vix"]),
        reading("yen_spec_net_contracts", s["yen_spec_net"]),
        Reading("yen_spec_cot_index", _at(s["yen_spec_index"], i)),
    ]
    flows, flows_pct = _flows(session, day)
    readings.append(
        Reading(f"jp_foreign_bond_buying_{FLOW_WEEKS}w_100m_yen", flows, None, flows_pct)
    )

    out = Gauge(
        day,
        readings,
        next_boj=_next_meeting(session, releases.BOJ_RELEASE_ID, day),
        next_fomc=_next_meeting(session, releases.FOMC_RELEASE_ID, day),
    )
    by_name = {r.name: r for r in readings}
    if (c := by_name["usd_jpy"].change_1m) is not None and c <= YEN_RALLY:
        out.flags.append(f"yen rallied: USD/JPY {c:+.1%} in a month")
    if (p := by_name["usd_jpy_vol_3m_pct"].percentile_3y) is not None and p >= HIGH_PERCENTILE:
        out.flags.append(f"USD/JPY volatility in its top decile ({p:.0%} percentile)")
    if (p := by_name["vix"].percentile_3y) is not None and p >= HIGH_PERCENTILE:
        out.flags.append(f"VIX in its top decile ({p:.0%} percentile)")
    index, net = by_name["yen_spec_cot_index"].value, by_name["yen_spec_net_contracts"].value
    if index is not None and net is not None and index <= CROWDED_INDEX and net < 0:
        out.flags.append(
            f"crowded: speculators' yen short near a 3-year extreme (index {index:.0f})"
        )
    narrowing = _change(diff_2y, i, QUARTER, relative=False)
    if narrowing is not None and narrowing <= NARROWING:
        out.flags.append(f"2-year differential narrowed {narrowing:+.2f} points in three months")
    if flows_pct is not None and flows_pct <= LOW_PERCENTILE:
        out.flags.append("repatriation: Japanese foreign bond buying in its bottom decile")
    for label, meeting in (("BOJ", out.next_boj), ("FOMC", out.next_fomc)):
        if meeting is not None and meeting - day <= MEETING_SOON:
            out.flags.append(f"{label} decision on {meeting}")
    return out
