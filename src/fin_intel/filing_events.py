"""Corporate events from SEC filings: what happened to a company, and when it became public.

Every SEC filing is listed in the filer's submissions (data.sec.gov), with its form, filing
date, acceptance time and, for 8-Ks, the items it reports. These are the labels research
needs: earnings releases (8-K item 2.02), acquisitions, bankruptcies, restatements,
executive changes, activist stakes, late filings, delistings. Events are dated by their
acceptance time, so studies can enter only after an event was public.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import CorporateEvent

# 8-K items (SEC Form 8-K General Instructions).
ITEMS = {
    "1.01": "Entry into a material agreement",
    "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership",
    "1.05": "Material cybersecurity incident",
    "2.01": "Acquisition or disposition completed",
    "2.02": "Results of operations (earnings release)",
    "2.03": "New material debt obligation",
    "2.04": "Debt acceleration",
    "2.05": "Exit or restructuring costs",
    "2.06": "Material impairment",
    "3.01": "Delisting notice or listing rule failure",
    "3.02": "Unregistered equity sale",
    "3.03": "Modification of shareholder rights",
    "4.01": "Change of auditor",
    "4.02": "Non-reliance on prior financials (restatement)",
    "5.01": "Change in control",
    "5.02": "Director or officer change",
    "5.03": "Charter or bylaw amendment; fiscal year change",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}
# Other forms that are events in themselves.
FORMS = {
    "SC 13D": "Activist stake (5%+, intent to influence)",
    "SC 13D/A": "Activist stake amended",
    "SC 13G": "Passive 5%+ stake",
    "SC 13G/A": "Passive stake amended",
    "SCHEDULE 13D": "Activist stake (5%+, intent to influence)",
    "SCHEDULE 13D/A": "Activist stake amended",
    "SCHEDULE 13G": "Passive 5%+ stake",
    "SCHEDULE 13G/A": "Passive stake amended",
    "NT 10-K": "Late annual report",
    "NT 10-Q": "Late quarterly report",
    "NT 20-F": "Late annual report (foreign filer)",
    "25-NSE": "Delisting from an exchange",
    "15-12B": "Deregistration",
    "15-12G": "Deregistration",
    "15-15D": "Suspension of reporting",
    "SC TO-T": "Third-party tender offer",
    "SC 14D9": "Target's response to a tender offer",
    "DEFM14A": "Merger proxy",
    "S-1": "IPO or offering registration",
    "F-1": "IPO or offering registration (foreign filer)",
}


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _accepted(value: str | None) -> datetime | None:
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
            if value
            else None
        )
    except ValueError:
        return None


def parse(filings: dict[str, Any], cik: int) -> list[dict[str, Any]]:
    """Event rows from a submissions table (the `recent` block or an older page): columns of
    parallel lists (accessionNumber, form, filingDate, acceptanceDateTime, items...)."""
    rows = []
    forms = filings.get("form") or []
    for i, form in enumerate(forms):
        is_8k = form in ("8-K", "8-K/A")
        if not is_8k and form not in FORMS:
            continue
        filed = _date((filings.get("filingDate") or [None] * len(forms))[i])
        if filed is None:
            continue
        base = {
            "accession": filings["accessionNumber"][i],
            "cik": cik,
            "form": form,
            "filed": filed,
            "accepted": _accepted((filings.get("acceptanceDateTime") or [None] * len(forms))[i]),
            "report_date": _date((filings.get("reportDate") or [None] * len(forms))[i]),
        }
        if is_8k:
            items = (filings.get("items") or [""] * len(forms))[i] or ""
            codes = [c.strip() for c in items.split(",") if c.strip()]
            events = [c for c in codes if c != "9.01"]  # 9.01 is just the exhibit list
            if not codes:
                events = ["?"]  # 8-Ks before the 2004 item numbering carry no item codes
            rows.extend({**base, "item": code} for code in events)
        else:
            rows.append({**base, "item": ""})
    return rows


def load(session: Session, rows: list[dict[str, Any]]) -> int:
    return upsert(session, CorporateEvent, rows, key=["accession", "item"], update=[])


def describe(form: str, item: str) -> str:
    return ITEMS.get(item, "8-K item " + item) if item else FORMS.get(form, form)
