from datetime import date

from sqlalchemy import select

from fin_intel import congress_members
from fin_intel.models import CommitteeMembership, CongressReport, CongressReportMember


def person(bioguide, first, last, terms, nickname=None):
    return {
        "id": {"bioguide": bioguide},
        "name": {"first": first, "last": last, "nickname": nickname},
        "terms": [
            {"type": t, "start": s, "end": e, "state": st, "district": d, "party": p}
            for t, s, e, st, d, p in terms
        ],
    }


LEGISLATORS = [
    person("R000011", "Nick", "Rahall", [("rep", "2013-01-03", "2015-01-03", "WV", 3, "Democrat")]),
    person(
        "J000001", "Evan", "Jenkins", [("rep", "2015-01-03", "2018-09-30", "WV", 3, "Republican")]
    ),
    person(
        "P000197", "Nancy", "Pelosi", [("rep", "2023-01-03", "2027-01-03", "CA", 11, "Democrat")]
    ),
    person(
        "T000476",
        "Thomas",
        "Tillis",
        [("sen", "2021-01-03", "2027-01-03", "NC", None, "Republican")],
        "Thom",
    ),
    person(
        "S000001", "Rick", "Scott", [("sen", "2019-01-08", "2031-01-03", "FL", None, "Republican")]
    ),
    person(
        "S000002", "Tim", "Scott", [("sen", "2023-01-03", "2029-01-03", "SC", None, "Republican")]
    ),
]


def report(session, doc_id, chamber, name, state, filed):
    session.add(
        CongressReport(
            doc_id=doc_id, chamber=chamber, name=name, state=state, filed=filed, year=filed.year
        )
    )


def test_reports_link_by_district_or_unique_name(session):
    congress_members.load_legislators(session, LEGISLATORS)
    report(session, "H1", "house", "Hon. Nancy Pelosi", "CA11", date(2026, 7, 1))
    report(session, "H2", "house", "Nick J. Rahall II", "WV03", date(2015, 1, 20))  # after the term
    report(session, "S1", "senate", "Thom Tillis", None, date(2026, 5, 4))
    report(session, "S2", "senate", "Scott, Rick", None, date(2026, 5, 4))
    report(session, "S3", "senate", "Scott", None, date(2026, 5, 4))  # two Scotts: unlinked
    session.commit()
    assert congress_members.link(session) == 4
    links = {
        r.doc_id: (r.bioguide, r.party, r.method)
        for r in session.scalars(select(CongressReportMember))
    }
    assert links == {
        "H1": ("P000197", "Democrat", "district"),
        # Rahall's final report, weeks after his term, while Jenkins held the seat.
        "H2": ("R000011", "Democrat", "district"),
        "S1": ("T000476", "Republican", "name"),
        "S2": ("S000001", "Republican", "name"),
    }


def test_committees_and_memberships_are_snapshots(session):
    committees = [
        {
            "thomas_id": "SSAS",
            "name": "Senate Committee on Armed Services",
            "type": "senate",
            "subcommittees": [{"thomas_id": "14", "name": "Cybersecurity"}],
        }
    ]
    congress_members.load_committees(session, committees)
    membership = {"SSAS": [{"bioguide": "T000476", "rank": 3, "party": "majority"}]}
    congress_members.load_memberships(session, membership)
    congress_members.load_memberships(session, {"SSAS14": [{"bioguide": "S000001", "rank": 1}]})
    rows = session.scalars(select(CommitteeMembership)).all()
    assert [(r.committee, r.bioguide) for r in rows] == [("SSAS14", "S000001")]


def test_trades_carry_party_and_filter_by_committee(session):
    from fin_intel import congress
    from fin_intel.models import CongressTrade

    congress_members.load_legislators(session, LEGISLATORS)
    report(session, "S1", "senate", "Thom Tillis", None, date(2026, 5, 4))
    report(session, "H1", "house", "Hon. Nancy Pelosi", "CA11", date(2026, 7, 1))
    for doc, ticker in (("S1", "LMT"), ("H1", "NVDA")):
        session.add(
            CongressTrade(
                key=doc, doc_id=doc, chamber="x", ticker=ticker, trans_date=date(2026, 4, 1)
            )
        )
    congress_members.load_memberships(session, {"SSAS": [{"bioguide": "T000476", "rank": 3}]})
    congress_members.link(session)
    session.commit()
    assert {t.ticker: t.party for t in congress.trades(session)} == {
        "LMT": "Republican",
        "NVDA": "Democrat",
    }
    assert [t.ticker for t in congress.trades(session, committee="ssas")] == ["LMT"]
    assert [t.ticker for t in congress.trades(session, party="dem")] == ["NVDA"]
