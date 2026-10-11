"""The supply-chain graph: industries, themes, and who supplies whom.

Derived from the BEA input-output tables (industries.py) and the themes (themes.py), and
rebuilt whenever either changes (`fin-intel build-graph`, also after `sync-industries`). Nodes:

- `industry:<code>`: BEA detail industries (~400, close to 6-digit NAICS), from the latest
  detail table (2017), with `part_of` edges to `summary:<code>` (71 industries, whose own
  `supplies` edges come from the latest summary table, 2023).
- `demand:<code>`: final demand (households, private investment, exports, federal defense
  and nondefense, state and local government): the end buyers.
- `theme:<name>`: the themes, with `maps_to` edges to the industries their NAICS codes
  cover (and curated BEA codes), and `supplies` edges aggregated from those industries.

A `supplies` edge carries the dollars, the share of the buyer's intermediate inputs
(`buyer_share`: how much the buyer depends on the supplier) and the share of the supplier's
total output sold to that buyer (`supplier_share`: how much the supplier depends on the
buyer). An industry's purchases from itself are left out.

A theme NAICS code narrower than a BEA industry (defense's 541715, R&D in physical sciences,
inside BEA's 541700, all scientific R&D) maps as `partial` and stays out of the theme's
aggregates, which would otherwise count all of the broader industry.

- `company:<cik>`: companies with a SIC code and a listed security, `maps_to` the BEA
  industries their SIC code reaches through Census's concordances (weighted by the share of
  NAICS codes landing in each), and `part_of` their themes (themes.themes_for). Construction
  companies stay unmapped (BEA splits construction by structure, not by NAICS), as do blank
  checks.
"""

from collections import defaultdict
from typing import Any

from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from fin_intel import themes, universe
from fin_intel.models import (
    Concordance,
    GraphEdge,
    GraphNode,
    IoCode,
    IoNaics,
    IoUse,
    Issuer,
    Security,
)

SUPPLIER_KINDS = ("industry",)
INPUT_KINDS = ("industry", "commodity")  # rows that count toward intermediate inputs


def _latest_year(session: Session, level: str) -> int | None:
    return session.scalar(
        select(IoUse.year).where(IoUse.level == level).order_by(IoUse.year.desc())
    )


def _flows(
    session: Session, level: str, year: int
) -> tuple[dict[tuple[str, str], float], dict[str, float], dict[str, float], dict[str, str]]:
    """Cells of one table, with each buyer's intermediate inputs and each row's total use."""
    kinds = dict(
        session.execute(select(IoCode.code, IoCode.kind).where(IoCode.level == level)).all()
    )
    cells: dict[tuple[str, str], float] = {}
    inputs: dict[str, float] = defaultdict(float)
    output: dict[str, float] = defaultdict(float)
    rows = session.execute(
        select(IoUse.commodity, IoUse.buyer, IoUse.value).where(
            IoUse.level == level, IoUse.year == year
        )
    )
    for commodity, buyer, value in rows:
        row_kind, col_kind = kinds.get(commodity), kinds.get(buyer)
        if col_kind == "industry" and row_kind in INPUT_KINDS:
            inputs[buyer] += value
        if row_kind == "industry" and col_kind in ("industry", "final_demand"):
            output[commodity] += value
            cells[(commodity, buyer)] = value
    return cells, inputs, output, kinds


def _supplies(
    prefix: str,
    cells: dict[tuple[str, str], float],
    inputs: dict[str, float],
    output: dict[str, float],
    kinds: dict[str, str],
    year: int,
) -> list[dict[str, Any]]:
    edges = []
    for (supplier, buyer), value in cells.items():
        if supplier == buyer:
            continue
        final = kinds.get(buyer) == "final_demand"
        edges.append(
            {
                "src": f"{prefix}:{supplier}",
                "dst": f"demand:{buyer}" if final else f"{prefix}:{buyer}",
                "kind": "supplies",
                "source": "bea",
                "year": year,
                "value": value,
                "buyer_share": None if final else _share(value, inputs.get(buyer)),
                "supplier_share": _share(value, output.get(supplier)),
                "note": None,
            }
        )
    return edges


def _share(value: float, total: float | None) -> float | None:
    return value / total if total and total > 0 else None


def theme_industries(session: Session) -> dict[str, dict[str, str]]:
    """Theme -> {BEA detail industry: "full" | "partial" | "curated"}."""
    naics = session.execute(select(IoNaics.code, IoNaics.naics)).all()
    detail = set(
        session.scalars(
            select(IoCode.code).where(IoCode.level == "detail", IoCode.kind == "industry")
        )
    )
    out: dict[str, dict[str, str]] = {}
    for theme in themes.THEMES:
        mapped: dict[str, str] = {}
        for prefix in theme.naics:
            for code, bea_naics in naics:
                if bea_naics.startswith(prefix):
                    mapped[code] = "full"
                # A theme code inside a broader BEA industry; skip sector-wide entries (all
                # construction is "23"), which the curated codes cover.
                elif prefix.startswith(bea_naics) and len(bea_naics) > 2:
                    mapped.setdefault(code, "partial")
        for code in theme.bea:
            if code in detail:
                mapped[code] = "curated"
        out[theme.name] = mapped
    return out


def build(session: Session) -> dict[str, int]:
    """Rebuild the graph tables from the IO tables and the themes."""
    session.execute(delete(GraphEdge))
    session.execute(delete(GraphNode))
    detail_year, summary_year = _latest_year(session, "detail"), _latest_year(session, "summary")
    if detail_year is None:
        return {"nodes": 0, "edges": 0}
    codes = session.execute(select(IoCode)).scalars().all()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for c in codes:
        if c.kind == "industry" and c.level in ("detail", "summary"):
            prefix = "industry" if c.level == "detail" else "summary"
            nodes[f"{prefix}:{c.code}"] = {
                "key": f"{prefix}:{c.code}",
                "kind": prefix,
                "label": c.title,
            }
            if c.level == "detail" and c.summary:
                edges.append(_edge(f"industry:{c.code}", f"summary:{c.summary}", "part_of", "bea"))
        elif c.kind == "final_demand" and c.level == "detail":
            nodes[f"demand:{c.code}"] = {
                "key": f"demand:{c.code}",
                "kind": "demand",
                "label": c.title,
            }

    cells, inputs, output, kinds = _flows(session, "detail", detail_year)
    edges += _supplies("industry", cells, inputs, output, kinds, detail_year)
    if summary_year is not None:
        s_cells, s_inputs, s_output, s_kinds = _flows(session, "summary", summary_year)
        # Summary final demand shares the detail codes' names (F06C00 vs F06C): keep the
        # summary graph to industries, the detail graph carries final demand.
        s_cells = {k: v for k, v in s_cells.items() if s_kinds.get(k[1]) == "industry"}
        edges += _supplies("summary", s_cells, s_inputs, s_output, s_kinds, summary_year)

    members = theme_industries(session)
    for theme in themes.THEMES:
        key = f"theme:{theme.name}"
        nodes[key] = {"key": key, "kind": "theme", "label": theme.label}
        for code, how in sorted(members[theme.name].items()):
            note = None if how == "full" else how
            edges.append(_edge(key, f"industry:{code}", "maps_to", "themes", note=note))
    edges += _theme_supplies(members, cells, inputs, output, kinds, detail_year)
    company_nodes, company_edges = _companies(session)
    nodes |= company_nodes
    edges += company_edges

    session.execute(insert(GraphNode), list(nodes.values()))
    known = set(nodes)
    edges = [e for e in edges if e["src"] in known and e["dst"] in known]
    for i in range(0, len(edges), 5000):
        session.execute(insert(GraphEdge), edges[i : i + 5000])
    return {"nodes": len(nodes), "edges": len(edges)}


CHAIN = ("sic1987", "naics2002", "naics2007", "naics2012", "naics2017")


def sic_industries(session: Session) -> dict[str, dict[str, float]]:
    """SIC code -> {BEA detail industry: weight}. Census gives no shares, so a code's weight
    splits equally among its targets at each concordance step (a NAICS code split in two
    later doesn't count twice), then each 2017 NAICS code goes to the BEA industries with
    its longest NAICS prefix (sector-wide entries skipped)."""
    steps: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for system, code, target in session.execute(
        select(Concordance.system, Concordance.code, Concordance.target_code)
    ):
        steps[system][code].add(target)
    bea = session.execute(select(IoNaics.naics, IoNaics.code)).all()
    out: dict[str, dict[str, float]] = {}
    for sic in steps["sic1987"]:
        weights: dict[str, float] = {sic: 1.0}
        for system in CHAIN[:-1]:
            nxt: dict[str, float] = defaultdict(float)
            for code, w in weights.items():
                targets = steps[system].get(code) or {code}
                for t in targets:
                    nxt[t] += w / len(targets)
            weights = nxt
        industries: dict[str, float] = defaultdict(float)
        for naics, w in weights.items():
            matches = [(len(p), c) for p, c in bea if naics.startswith(p) and len(p) > 2]
            if matches:
                longest = max(n for n, _ in matches)
                best = {c for n, c in matches if n == longest}
                for c in best:
                    industries[c] += w / len(best)
        total = sum(industries.values())
        if total:
            out[sic] = {c: w / total for c, w in industries.items()}
    return out


# SEC codes for vehicles, not industries: blank checks (SPACs), depositary receipt programs.
NOT_INDUSTRIES = {6770, 8880}


def _sic_lookup(table: dict[str, dict[str, float]], sic: int) -> dict[str, float] | None:
    """SEC uses codes the 1987 SIC lacks: group codes ending in 0 (7370: computer
    services) and its own (3576 computer communications equipment, 6199 finance services).
    Those map to their 3-digit group's codes, else their 2-digit group's, weighted equally."""
    code = f"{sic:04d}"
    if sic in NOT_INDUSTRIES:
        return None
    if code in table:
        return table[code]
    for stem in (code[:3], code[:2]):
        group = [weights for s, weights in table.items() if s.startswith(stem)]
        if group:
            merged: dict[str, float] = defaultdict(float)
            for weights in group:
                for c, w in weights.items():
                    merged[c] += w / len(group)
            return dict(merged)
    return None


def _companies(session: Session) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    table = sic_industries(session)
    members = {m.cik: m.industry for m in universe.read()}
    rows = session.execute(
        select(Issuer.cik, Issuer.name, Issuer.sic, Security.ticker)
        .join(Security, Security.cik == Issuer.cik)
        .where(Issuer.sic.is_not(None), Security.active, Security.ticker.is_not(None))
        .order_by(Issuer.cik, Security.ticker)
    ).all()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for cik, name, sic, ticker in rows:
        key = f"company:{cik}"
        if key in nodes:
            continue
        nodes[key] = {"key": key, "kind": "company", "label": f"{name} ({ticker})"}
        for code, weight in sorted((_sic_lookup(table, sic) or {}).items()):
            edges.append(
                _edge(key, f"industry:{code}", "maps_to", "sic", value=weight, note=f"sic {sic}")
            )
        for theme in themes.themes_for(sic, members.get(cik)):
            how = "universe" if members.get(cik) else "sic"
            edges.append(_edge(key, f"theme:{theme}", "part_of", "themes", note=how))
    return nodes, edges


def companies(session: Session, key: str) -> list[dict[str, Any]]:
    """Companies in an industry (by mapping weight) or a theme."""
    edge_kind = "part_of" if key.startswith("theme:") else "maps_to"
    rows = session.execute(
        select(GraphEdge.src, GraphNode.label, GraphEdge.value, GraphEdge.note)
        .join(GraphNode, GraphNode.key == GraphEdge.src)
        .where(GraphEdge.dst == key, GraphEdge.kind == edge_kind, GraphNode.kind == "company")
        .order_by(GraphEdge.value.desc().nulls_last(), GraphNode.label)
    ).all()
    return [{"node": n, "label": label, "weight": w, "note": note} for n, label, w, note in rows]


def _edge(src: str, dst: str, kind: str, source: str, **extra: Any) -> dict[str, Any]:
    return {
        "src": src,
        "dst": dst,
        "kind": kind,
        "source": source,
        "year": None,
        "value": None,
        "buyer_share": None,
        "supplier_share": None,
        "note": None,
    } | extra


def _theme_supplies(
    members: dict[str, dict[str, str]],
    cells: dict[tuple[str, str], float],
    inputs: dict[str, float],
    output: dict[str, float],
    kinds: dict[str, str],
    year: int,
) -> list[dict[str, Any]]:
    """Theme-to-theme and theme-to-final-demand flows, summed over the themes' industries
    (full and curated maps). Industries in two themes count in both."""
    full = {
        name: {c for c, how in mapped.items() if how != "partial"}
        for name, mapped in members.items()
    }
    of: dict[str, set[str]] = defaultdict(set)
    for name, industries in full.items():
        for code in industries:
            of[code].add(name)
    flows: dict[tuple[str, str], float] = defaultdict(float)
    for (supplier, buyer), value in cells.items():
        if supplier == buyer:
            continue
        buyers = (
            {f"demand:{buyer}"}
            if kinds.get(buyer) == "final_demand"
            else {f"theme:{t}" for t in of.get(buyer, ())}
        )
        for s in of.get(supplier, ()):
            for b in buyers:
                if b != f"theme:{s}":
                    flows[(f"theme:{s}", b)] += value
    theme_inputs = {f"theme:{n}": sum(inputs.get(c, 0) for c in cs) for n, cs in full.items()}
    theme_output = {f"theme:{n}": sum(output.get(c, 0) for c in cs) for n, cs in full.items()}
    return [
        _edge(
            src,
            dst,
            "supplies",
            "bea",
            year=year,
            value=value,
            buyer_share=_share(value, theme_inputs.get(dst)),
            supplier_share=_share(value, theme_output.get(src)),
        )
        for (src, dst), value in flows.items()
    ]


# --- queries ---

_WALK = """
WITH RECURSIVE walk(node, depth, share, path) AS (
    SELECT CAST(:start AS text), 0, CAST(1.0 AS float8), ARRAY[CAST(:start AS text)]
    UNION ALL
    SELECT e.{next}, w.depth + 1, w.share * e.{share}, w.path || e.{next}
    FROM walk w JOIN graph_edges e ON e.{here} = w.node AND e.kind = 'supplies'
    WHERE w.depth < :hops AND e.{share} >= :min_share AND NOT e.{next} = ANY(w.path)
)
SELECT DISTINCT ON (w.node) w.node, n.label, n.kind, w.depth, w.share, w.path
FROM walk w JOIN graph_nodes n ON n.key = w.node
WHERE w.depth > 0
ORDER BY w.node, w.share DESC
"""


def walk(
    session: Session, start: str, direction: str, hops: int = 2, min_share: float = 0.02
) -> list[dict[str, Any]]:
    """Suppliers (`upstream`) or buyers (`downstream`) of a node within `hops` edges.

    Upstream follows edges where the supplier is at least `min_share` of the buyer's inputs;
    `share` is the product along the path (a supplier of 10% of a supplier of 30% scores
    3%). Downstream follows edges taking at least `min_share` of the supplier's output.
    Each node appears once, by its strongest path.
    """
    if direction == "upstream":
        sql = _WALK.format(next="src", here="dst", share="buyer_share")
    elif direction == "downstream":
        sql = _WALK.format(next="dst", here="src", share="supplier_share")
    else:
        raise ValueError(f"direction must be upstream or downstream, not {direction!r}")
    rows = session.execute(text(sql), {"start": start, "hops": hops, "min_share": min_share})
    out = [dict(r._mapping) for r in rows]
    return sorted(out, key=lambda r: -r["share"])


def resolve(session: Session, ident: str) -> str:
    """A node key from `theme:defense`, `industry:334413`, a theme name or a BEA code."""
    candidates = (
        [ident] if ":" in ident else [f"theme:{ident}", f"industry:{ident}", f"summary:{ident}"]
    )
    for key in candidates:
        if session.get(GraphNode, key) is not None:
            return key
    raise ValueError(f"no graph node {ident!r}; run fin-intel sync-industries (or build-graph)")


def neighbors(session: Session, key: str) -> dict[str, list[dict[str, Any]]]:
    """A node's direct edges by kind and direction."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for direction, mine, other in (
        ("out", GraphEdge.src, GraphEdge.dst),
        ("in", GraphEdge.dst, GraphEdge.src),
    ):
        rows = session.execute(
            select(GraphEdge, GraphNode.label)
            .join(GraphNode, GraphNode.key == other)
            .where(mine == key)
        ).all()
        for edge, label in rows:
            out[f"{edge.kind}_{direction}"].append(
                {
                    "node": edge.dst if direction == "out" else edge.src,
                    "label": label,
                    "value": edge.value,
                    "buyer_share": edge.buyer_share,
                    "supplier_share": edge.supplier_share,
                    "year": edge.year,
                    "note": edge.note,
                }
            )
    return dict(out)
