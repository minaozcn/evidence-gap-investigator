"""Read-only investigation tools the model can call.

Every function takes an open DB connection and returns a JSON-serialisable
dict. All SQL is parameterised. Answer-leak rules:
  - get_incident never returns root_cause / root_cause_category.
  - get_evidence returns only evidence rows, never incident root causes.
  - find_similar_incidents returns only OTHER, RESOLVED incidents; only
    those historical incidents carry a root cause.
"""

import re

from .llm.base import ToolSpec

MAX_EVIDENCE_ROWS = 200
MAX_SIMILAR_RESULTS = 5

# Words that are too generic to make two incident titles "similar" on their own.
GENERIC_TITLE_WORDS = frozenset("""
    a an the and or on in of to for with after during from by at is are was were
    error errors failure failures failing failed fail issue issues incident
    incidents problem problems service services api high elevated returning
    spike spikes down slow
""".split())

DATA_GAP_NOTE = ("MISSING DATA: records for this window were never collected or "
                 "never received. "
                 "This is not evidence for or against any hypothesis; it only "
                 "shows what data should be collected.")


def _iso(value):
    """DATETIME values are stored in UTC; mark them explicitly with 'Z'."""
    return value.isoformat() + "Z" if value is not None else None


def _find_incident_row(conn, incident_key: str):
    with conn.cursor() as cur:
        cur.execute("SELECT id, incident_key, title, service FROM incidents "
                    "WHERE incident_key = %s", (incident_key,))
        return cur.fetchone()


# --- get_incident -------------------------------------------------------------

def get_incident(conn, incident_key: str) -> dict:
    # root_cause / root_cause_category are deliberately NOT selected: the
    # model must never see the answer for the incident it investigates.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT incident_key, title, summary, service, severity, status, "
            "started_at, detected_at, resolved_at "
            "FROM incidents WHERE incident_key = %s",
            (incident_key,),
        )
        row = cur.fetchone()
    if row is None:
        return {"error": f"No incident with key {incident_key}"}
    return {k: (_iso(v) if hasattr(v, "isoformat") else v) for k, v in row.items()}


# --- get_evidence ---------------------------------------------------------------

def get_evidence(conn, incident_key: str) -> dict:
    incident = _find_incident_row(conn, incident_key)
    if incident is None:
        return {"error": f"No incident with key {incident_key}"}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, evidence_kind, source_type, source_name, observed_at, "
            "observed_until, collected_at, content, limitations, reliability "
            "FROM evidence WHERE incident_id = %s "
            "ORDER BY observed_at, id LIMIT %s",
            (incident["id"], MAX_EVIDENCE_ROWS + 1),
        )
        rows = cur.fetchall()

    truncated = len(rows) > MAX_EVIDENCE_ROWS
    evidence, data_gaps = [], []
    for r in rows[:MAX_EVIDENCE_ROWS]:
        item = {
            "kind": r["evidence_kind"],
            "source_type": r["source_type"],
            "source_name": r["source_name"],
            "observed_at": _iso(r["observed_at"]),
            "observed_until": _iso(r["observed_until"]),
            "collected_at": _iso(r["collected_at"]),
            "content": r["content"],
            "limitations": r["limitations"],
            "reliability": r["reliability"],
        }
        if r["evidence_kind"] == "data_gap":
            # Separate list and a different id prefix, so a gap can never be
            # cited as supporting or contradicting evidence.
            data_gaps.append({"gap_id": f"GAP-{r['id']}", **item, "note": DATA_GAP_NOTE})
        else:
            evidence.append({"evidence_id": f"E-{r['id']}", **item})

    return {
        "incident_key": incident["incident_key"],
        "evidence": evidence,
        "data_gaps": data_gaps,
        "truncated": truncated,
        "how_to_read": (
            "All times are UTC. 'evidence' is ordered by observed_at. "
            "kind=observation: something was seen. "
            "kind=negative_finding: a complete source was checked for the "
            "window and nothing was found; this weakens a hypothesis but does "
            "not rule it out. 'data_gaps' lists windows with missing data; "
            "only E- ids may be cited as evidence, never GAP- ids. "
            "'limitations' says what an item can NOT show."
        ),
    }


# --- find_similar_incidents -------------------------------------------------------

def title_words(title: str, service: str) -> set[str]:
    """Meaningful words of a title: lower-case, without generic words and
    without the service name itself (that is matched separately)."""
    service_words = set(re.findall(r"[a-z0-9]+", service.lower()))
    words = set(re.findall(r"[a-z0-9]+", title.lower()))
    return {w for w in words
            if len(w) >= 3 and w not in GENERIC_TITLE_WORDS and w not in service_words}


SIMILARITY_METHOD = (
    "A past incident is listed if it is resolved, is not the target incident, "
    "and either runs on the same service or shares at least one meaningful "
    "title word with the target. Generic words (e.g. 'errors', 'failures', "
    "'service') and the service name itself are ignored. Results are ranked by "
    "same service first, then by number of shared words, then most recent. "
    "Similar past causes are hypotheses to check, not answers."
)


def find_similar_incidents(conn, incident_key: str) -> dict:
    target = _find_incident_row(conn, incident_key)
    if target is None:
        return {"error": f"No incident with key {incident_key}"}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT incident_key, title, service, severity, started_at, resolved_at, "
            "root_cause, root_cause_category FROM incidents "
            "WHERE status = 'resolved' AND id <> %s",
            (target["id"],),
        )
        candidates = cur.fetchall()

    target_words = title_words(target["title"], target["service"])
    matches = []
    for c in candidates:
        if c["incident_key"] == target["incident_key"]:   # defence in depth
            continue
        same_service = c["service"] == target["service"]
        shared = sorted(target_words & title_words(c["title"], c["service"]))
        if not (same_service or shared):
            continue
        matches.append({
            "incident_key": c["incident_key"],
            "title": c["title"],
            "service": c["service"],
            "severity": c["severity"],
            "started_at": _iso(c["started_at"]),
            "resolved_at": _iso(c["resolved_at"]),
            "root_cause": c["root_cause"],
            "root_cause_category": c["root_cause_category"],
            "match_reasons": {"same_service": same_service, "shared_title_words": shared},
        })

    matches.sort(key=lambda m: (m["match_reasons"]["same_service"],
                                len(m["match_reasons"]["shared_title_words"]),
                                m["started_at"]), reverse=True)
    return {
        "target_incident_key": target["incident_key"],
        "similarity_method": SIMILARITY_METHOD,
        "similar_incidents": matches[:MAX_SIMILAR_RESULTS],
    }


# --- Tool specs and dispatch --------------------------------------------------------

_KEY_PARAMS = {
    "type": "object",
    "properties": {
        "incident_key": {"type": "string", "description": "Incident key, e.g. INC-1001"},
    },
    "required": ["incident_key"],
}

GET_INCIDENT = ToolSpec(
    name="get_incident",
    description="Look up one incident by its key (for example INC-1001) "
                "and return its title, summary, service, severity, status and times.",
    parameters=_KEY_PARAMS,
)

GET_EVIDENCE = ToolSpec(
    name="get_evidence",
    description="Return the evidence recorded for an incident, ordered by time (UTC), "
                "with source, reliability and limitations. Windows with missing data "
                "are returned separately under 'data_gaps' and are not evidence.",
    parameters=_KEY_PARAMS,
)

FIND_SIMILAR_INCIDENTS = ToolSpec(
    name="find_similar_incidents",
    description="Find resolved past incidents similar to the given incident (same "
                "service or shared meaningful title words), including their confirmed "
                "root causes. Explains why each one matched.",
    parameters=_KEY_PARAMS,
)

TOOLS = {
    GET_INCIDENT.name: (GET_INCIDENT, get_incident),
    GET_EVIDENCE.name: (GET_EVIDENCE, get_evidence),
    FIND_SIMILAR_INCIDENTS.name: (FIND_SIMILAR_INCIDENTS, find_similar_incidents),
}
TOOL_SPECS = [spec for spec, _ in TOOLS.values()]


def execute_tool(conn, name: str, args: dict) -> dict:
    """Run a tool requested by the model. Bad requests return an error dict."""
    if name not in TOOLS:
        return {"error": f"Unknown tool {name}"}
    key = args.get("incident_key") if isinstance(args, dict) else None
    if not isinstance(key, str) or set(args) != {"incident_key"}:
        return {"error": f"{name} expects exactly one string argument: incident_key"}
    return TOOLS[name][1](conn, key)
