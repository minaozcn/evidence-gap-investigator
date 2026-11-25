"""Strict validation of the model's submit_investigation output.

The model's output is never trusted: every field, type, enum value, id and
status rule is checked here. validate_submission returns a list of
human-readable violations; an empty list means the submission is valid.
"""

import re
from dataclasses import dataclass, field

VERDICTS = ("conclusive", "inconclusive")
CONFIDENCES = ("low", "medium", "high")
STATUSES = ("leading", "plausible", "weakened", "ruled_out")
CATEGORIES = ("config_change", "resource_exhaustion", "third_party",
              "infrastructure", "expired_credential", "other")

MAX_HYPOTHESES = 6
MAX_NEXT_CHECKS = 10
MAX_TEXT = 2000

TOP_KEYS = {"incident_key", "verdict", "summary", "hypotheses", "next_checks"}
HYPOTHESIS_KEYS = {"id", "title", "root_cause_category", "confidence", "status",
                   "supporting_evidence", "contradicting_evidence", "related_history"}
EVIDENCE_REF_KEYS = {"evidence_id", "reason"}
NEXT_CHECK_KEYS = {"description", "why", "hypotheses", "based_on_gaps"}

HYPOTHESIS_ID = re.compile(r"^H[1-9][0-9]?$")


@dataclass
class ValidationContext:
    """What the agent actually saw during THIS investigation."""
    incident_key: str
    evidence_kinds: dict[str, str] = field(default_factory=dict)  # "E-12" -> kind
    gap_ids: set[str] = field(default_factory=set)                # "GAP-31"
    similar_keys: set[str] = field(default_factory=set)           # returned by find_similar


def _check_keys(obj, expected: set, where: str, errors: list) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be an object")
        return False
    missing = expected - obj.keys()
    extra = obj.keys() - expected
    if missing:
        errors.append(f"{where}: missing field(s) {sorted(missing)}")
    if extra:
        errors.append(f"{where}: unexpected field(s) {sorted(extra)}")
    return not missing


def _text(value, where: str, errors: list):
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string")
    elif len(value) > MAX_TEXT:
        errors.append(f"{where}: longer than {MAX_TEXT} characters")


def _enum(value, allowed, where: str, errors: list):
    if not isinstance(value, str) or value not in allowed:
        errors.append(f"{where}: must be one of {list(allowed)}, got {value!r}")


def _list(value, where: str, errors: list) -> list:
    if not isinstance(value, list):
        errors.append(f"{where}: must be a list")
        return []
    return value


def _evidence_refs(refs, where: str, ctx: ValidationContext, errors: list) -> list[str]:
    """Validate a supporting/contradicting list; return the valid E- ids."""
    valid, seen = [], set()
    for i, ref in enumerate(_list(refs, where, errors)):
        w = f"{where}[{i}]"
        if not _check_keys(ref, EVIDENCE_REF_KEYS, w, errors):
            continue
        eid = ref["evidence_id"]
        _text(ref["reason"], f"{w}.reason", errors)
        if not isinstance(eid, str):
            errors.append(f"{w}.evidence_id: must be a string")
        elif eid in ctx.gap_ids or eid.startswith("GAP-"):
            errors.append(f"{w}.evidence_id: {eid} is missing data (data_gap) and can "
                          "never be supporting or contradicting evidence; use it only "
                          "in next_checks.based_on_gaps")
        elif eid not in ctx.evidence_kinds:
            errors.append(f"{w}.evidence_id: {eid} is not an evidence id returned by "
                          f"get_evidence for {ctx.incident_key}")
        elif eid in seen:
            errors.append(f"{w}.evidence_id: {eid} is listed twice")
        else:
            seen.add(eid)
            valid.append(eid)
    return valid


def validate_submission(sub, ctx: ValidationContext) -> list[str]:
    errors: list[str] = []
    if not _check_keys(sub, TOP_KEYS, "submission", errors):
        return errors

    if sub["incident_key"] != ctx.incident_key:
        errors.append(f"incident_key: must be {ctx.incident_key!r}, got {sub['incident_key']!r}")
    _enum(sub["verdict"], VERDICTS, "verdict", errors)
    _text(sub["summary"], "summary", errors)

    hypotheses = _list(sub["hypotheses"], "hypotheses", errors)
    if isinstance(sub["hypotheses"], list) and not 1 <= len(hypotheses) <= MAX_HYPOTHESES:
        errors.append(f"hypotheses: must contain 1 to {MAX_HYPOTHESES} items")

    ids, valid_hyps = set(), []
    for i, h in enumerate(hypotheses):
        w = f"hypotheses[{i}]"
        if not _check_keys(h, HYPOTHESIS_KEYS, w, errors):
            continue
        hid = h["id"]
        if not isinstance(hid, str) or not HYPOTHESIS_ID.match(hid):
            errors.append(f"{w}.id: must look like H1, H2, ... got {hid!r}")
        elif hid in ids:
            errors.append(f"{w}.id: duplicate hypothesis id {hid}")
        else:
            ids.add(hid)
        _text(h["title"], f"{w}.title", errors)
        _enum(h["root_cause_category"], CATEGORIES, f"{w}.root_cause_category", errors)
        _enum(h["confidence"], CONFIDENCES, f"{w}.confidence", errors)
        _enum(h["status"], STATUSES, f"{w}.status", errors)

        support = _evidence_refs(h["supporting_evidence"], f"{w}.supporting_evidence", ctx, errors)
        contra = _evidence_refs(h["contradicting_evidence"], f"{w}.contradicting_evidence", ctx, errors)
        both = set(support) & set(contra)
        if both:
            errors.append(f"{w}: evidence {sorted(both)} is both supporting and contradicting")

        history = _list(h["related_history"], f"{w}.related_history", errors)
        seen_hist = set()
        for j, key in enumerate(history):
            if not isinstance(key, str):
                errors.append(f"{w}.related_history[{j}]: must be a string")
            elif key == ctx.incident_key:
                errors.append(f"{w}.related_history[{j}]: cannot reference the target incident")
            elif key not in ctx.similar_keys:
                errors.append(f"{w}.related_history[{j}]: {key} was not returned by "
                              "find_similar_incidents in this investigation")
            elif key in seen_hist:
                errors.append(f"{w}.related_history[{j}]: {key} is listed twice")
            else:
                seen_hist.add(key)

        # Status / confidence consistency
        status, conf = h["status"], h["confidence"]
        observed_contra = [e for e in contra if ctx.evidence_kinds[e] == "observation"]
        if status == "ruled_out":
            if not observed_contra:
                errors.append(f"{w}: ruled_out needs at least one contradicting evidence of "
                              "kind 'observation'; negative_finding alone can only weaken")
            if conf != "low":
                errors.append(f"{w}: a ruled_out hypothesis must have confidence 'low'")
        if status == "weakened" and not contra:
            errors.append(f"{w}: weakened needs at least one contradicting evidence")
        if conf in ("medium", "high") and not support:
            errors.append(f"{w}: confidence {conf!r} needs at least one supporting evidence "
                          "from this incident; similar past incidents alone are not enough")
        valid_hyps.append(h)

    # Verdict consistency
    leading = [h for h in valid_hyps if h["status"] == "leading"]
    if len(leading) > 1:
        errors.append("hypotheses: at most one hypothesis may be 'leading'")
    if sub["verdict"] == "conclusive":
        if len(leading) != 1:
            errors.append("verdict conclusive: exactly one 'leading' hypothesis is required")
        elif leading[0]["confidence"] != "high" or not leading[0]["supporting_evidence"]:
            errors.append("verdict conclusive: the leading hypothesis needs confidence "
                          "'high' and at least one supporting evidence")
    if sub["verdict"] == "inconclusive":
        if any(h["confidence"] == "high" for h in valid_hyps):
            errors.append("verdict inconclusive: no hypothesis may have confidence 'high'")
        if isinstance(sub["next_checks"], list) and not sub["next_checks"]:
            errors.append("verdict inconclusive: next_checks must not be empty")

    # Next checks
    checks = _list(sub["next_checks"], "next_checks", errors)
    if len(checks) > MAX_NEXT_CHECKS:
        errors.append(f"next_checks: at most {MAX_NEXT_CHECKS} items")
    for i, c in enumerate(checks):
        w = f"next_checks[{i}]"
        if not _check_keys(c, NEXT_CHECK_KEYS, w, errors):
            continue
        _text(c["description"], f"{w}.description", errors)
        _text(c["why"], f"{w}.why", errors)
        for j, hid in enumerate(_list(c["hypotheses"], f"{w}.hypotheses", errors)):
            if not isinstance(hid, str) or hid not in ids:
                errors.append(f"{w}.hypotheses[{j}]: unknown hypothesis id {hid!r}")
        for j, gid in enumerate(_list(c["based_on_gaps"], f"{w}.based_on_gaps", errors)):
            if not isinstance(gid, str) or gid not in ctx.gap_ids:
                errors.append(f"{w}.based_on_gaps[{j}]: {gid!r} is not a GAP id returned "
                              f"by get_evidence for {ctx.incident_key}")
    return errors
