"""Settings saved from the dashboard: provider keys, the SEC identity, the watchlist.

They live in the database (`app_settings`) and apply over the environment (`get_settings`),
so a new key works on the next step without editing .env or redeploying; deleting one
falls back to .env. Saving a key retries the steps it blocked. Values are stored as given
(the keys are for free services) and reach the database backups with everything else.

A key's status comes from what the feeds that use it did last: rejected (HTTP 401/403 or a
step blocked on it), working (a recent success), missing, or unused.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel.models import AppSetting, JobState


@dataclass(frozen=True)
class Editable:
    label: str
    group: str  # keys, identity, watchlist
    providers: tuple[str, ...] = ()  # catalog providers that use it
    secret: bool = True
    required: bool = False  # steps are blocked without it
    help: str = ""
    url: str | None = None  # where to get one


EDITABLE: dict[str, Editable] = {
    "sec_user_agent": Editable(
        "SEC identity",
        "identity",
        ("sec",),
        secret=False,
        required=True,
        help="SEC asks every client to identify itself: 'Your Name you@example.com'.",
        url="https://www.sec.gov/os/accessing-edgar-data",
    ),
    "fred_api_key": Editable(
        "FRED",
        "keys",
        ("fred",),
        required=True,
        help="Economic series, rates, FX for foreign filers' metrics.",
        url="https://fred.stlouisfed.org/docs/api/api_key.html",
    ),
    "tiingo_api_key": Editable(
        "Tiingo",
        "keys",
        ("tiingo",),
        required=True,
        help="Long daily price history and dividends for the watchlist and holdings.",
        url="https://www.tiingo.com/account/api/token",
    ),
    "massive_api_key": Editable(
        "Massive",
        "keys",
        ("massive",),
        required=True,
        help="Daily bars for every US security, tickers, corporate actions (free: 5/min).",
        url="https://massive.com/dashboard",
    ),
    "edinet_api_key": Editable(
        "EDINET",
        "keys",
        ("edinet",),
        required=True,
        help="Japanese filings (FSA).",
        url="https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1",
    ),
    "opendart_api_key": Editable(
        "OpenDART",
        "keys",
        ("dart",),
        required=True,
        help="Korean filings (FSS), about 20k requests a day.",
        url="https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do",
    ),
    "eia_api_key": Editable(
        "EIA",
        "keys",
        ("eia",),
        help="US energy data. Without one, the shared DEMO_KEY is used (rate-limited).",
        url="https://www.eia.gov/opendata/register.php",
    ),
    "lda_api_key": Editable(
        "Senate LDA",
        "keys",
        ("lda",),
        help="Lobbying disclosures; anonymous access is very slow.",
        url="https://lda.senate.gov/api/register/",
    ),
    "congress_api_key": Editable(
        "api.data.gov (Congress)",
        "keys",
        ("congress",),
        help="Bills and committees from api.congress.gov.",
        url="https://api.data.gov/signup/",
    ),
    "openfigi_api_key": Editable(
        "OpenFIGI",
        "keys",
        ("openfigi",),
        help="Optional: raises OpenFIGI's limits for identifier mapping.",
        url="https://www.openfigi.com/api",
    ),
    "acled_email": Editable(
        "ACLED account email",
        "keys",
        ("acled",),
        secret=False,
        help="Conflict events (acleddata.com account).",
        url="https://acleddata.com/register/",
    ),
    "acled_password": Editable("ACLED account password", "keys", ("acled",)),
    "watchlist": Editable(
        "Watchlist",
        "watchlist",
        secret=False,
        help="Tickers for long price history and SEC fundamentals, comma-separated.",
    ),
}
DEFAULTS = {"eia_api_key": "DEMO_KEY"}


def env_name(name: str) -> str:
    return f"FI_{name.upper()}"


def mask(value: str) -> str:
    return "•" * 8 + value[-4:] if len(value) > 8 else "•" * len(value)


def save(session: Session, name: str, value: str, now: datetime | None = None) -> list[str]:
    """Save a setting and retry the steps that were blocked or refused credentials;
    returns their names."""
    from fin_intel import jobs
    from fin_intel.config import get_settings

    if name not in EDITABLE:
        raise KeyError(name)
    now = now or datetime.now(UTC)
    value = value.strip()
    if name == "watchlist":
        value = ",".join(
            t.strip().upper() for t in value.replace("\n", ",").split(",") if t.strip()
        )
    row = session.get(AppSetting, name)
    if row is None:
        session.add(AppSetting(name=name, value=value, updated_at=now))
    else:
        row.value, row.updated_at = value, now
    session.commit()
    get_settings.cache_clear()
    retried = list(
        session.scalars(
            select(JobState.name).where(
                (JobState.status == jobs.BLOCKED) | (JobState.error_kind == "auth")
            )
        )
    )
    for step in retried:
        jobs.retry_now(session, step, now)
    return retried


def forget(session: Session, name: str) -> None:
    """Drop a saved setting: the environment's value applies again."""
    from fin_intel.config import get_settings

    row = session.get(AppSetting, name)
    if row is not None:
        session.delete(row)
        session.commit()
    get_settings.cache_clear()


def overview(session: Session) -> list[dict[str, Any]]:
    """Every editable setting: where its value comes from, masked, and its status."""
    from fin_intel import catalog
    from fin_intel.config import Settings, get_settings

    environment = Settings()  # without the saved values
    current = get_settings()
    saved = {r.name: r for r in session.scalars(select(AppSetting))}
    feeds = catalog.feeds(session)
    problems = [
        s
        for s in session.scalars(
            select(JobState).where(JobState.error_kind.in_(("auth", "not_configured")))
        )
    ]
    out = []
    for name, spec in EDITABLE.items():
        value = getattr(current, name) or None
        if name in saved:
            source = "dashboard"
        elif value is None:
            source = None
        elif value == DEFAULTS.get(name) and getattr(environment, name) == DEFAULTS[name]:
            source = "default"
        else:
            source = "env"
        status, detail, last_used = _status(name, spec, value, source, feeds, problems)
        out.append(
            {
                "name": name,
                "env": env_name(name),
                "label": spec.label,
                "group": spec.group,
                "help": spec.help,
                "url": spec.url,
                "secret": spec.secret,
                "required": spec.required,
                "source": source,
                "value": None if value is None else mask(value) if spec.secret else value,
                "updated_at": saved[name].updated_at if name in saved else None,
                "status": status,
                "detail": detail,
                "last_used": last_used,
            }
        )
    return out


def _status(name, spec, value, source, feeds, problems) -> tuple[str, str | None, Any]:
    env = env_name(name)
    for state in problems:
        error = state.error or ""
        if env in error or (
            state.error_kind == "auth" and any(p in error.lower() for p in spec.providers)
        ):
            kind = "missing" if state.error_kind == "not_configured" else "rejected"
            return kind, f"{state.name}: {error}", None
    used = [f for f in feeds if f.provider in spec.providers]
    for feed in used:
        error = feed.last_error or ""
        if any(code in error for code in ("401", "403")):  # an item still failing on it
            return "rejected", f"{feed.provider}/{feed.dataset}: {error}", None
    if value is None:
        return ("missing" if spec.required else "not set"), None, None
    successes = [f.last_success for f in used if f.last_success]
    if successes:
        return "working", None, max(successes)
    return ("default" if source == "default" else "unused"), None, None
