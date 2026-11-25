"""Compare investigation runs with the expected outcomes in tests/fixtures.

This is the ONLY code that reads expected_outcomes.json; the agent and its
tools never do. Uses no API.

    python -m scripts.evaluate                 # latest run per incident in runs/
    python -m scripts.evaluate runs/x.json ... # specific run files
"""

import json
import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = AGENT_ROOT / "tests" / "fixtures" / "expected_outcomes.json"

KEYWORD_LIMITATION = (
    "Next-check matching uses keyword substrings, one expected item per "
    "suggested check: it can over-count (a keyword "
    "used in another sense) and under-count (synonyms not in the list). Review "
    "the actual next_checks text before trusting this score.")


def load_expectations() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["investigations"]


def _check(checks, name, passed, detail=""):
    checks.append({"check": name, "passed": bool(passed), "detail": detail})


def _matches(text: str, all_of: list[list[str]]) -> bool:
    return all(any(k in text for k in group) for group in all_of)


def match_next_checks(texts: list[str], expected: list[dict]) -> list[str]:
    """One-to-one matching: each next_check counts for at most one expected
    item (maximum bipartite matching). Returns the matched expected labels."""
    candidates = [[t for t, text in enumerate(texts) if _matches(text, e["all_of"])]
                  for e in expected]
    owner: dict[int, int] = {}          # text index -> expected index

    def assign(e, seen):
        for t in candidates[e]:
            if t in seen:
                continue
            seen.add(t)
            if t not in owner or assign(owner[t], seen):
                owner[t] = e
                return True
        return False

    matched = [i for i in range(len(expected)) if assign(i, set())]
    return [expected[i]["label"] for i in matched]


def evaluate_run(run: dict, expectations: dict) -> dict:
    key = run.get("incident_key")
    checks: list[dict] = []
    result = {"incident_key": key, "checks": checks}

    if key not in expectations:
        _check(checks, "has expectations", False, f"no expected outcome for {key}")
        result["passed"] = False
        return result
    exp = expectations[key]

    if run.get("status") != "completed":
        failure = run.get("failure") or {}
        _check(checks, "investigation completed", False,
               f"status={run.get('status')} reason={failure.get('reason')} "
               f"violations={failure.get('violations')}")
        result["passed"] = False
        return result
    _check(checks, "investigation completed", True)

    inv = run["investigation"]
    hyps = inv["hypotheses"]
    _check(checks, "verdict", inv["verdict"] == exp["expected_verdict"],
           f"expected {exp['expected_verdict']}, got {inv['verdict']}")

    # Defence in depth: the validator already rejects these.
    cited = [r["evidence_id"] for h in hyps
             for r in h["supporting_evidence"] + h["contradicting_evidence"]]
    _check(checks, "no GAP- id cited as evidence",
           not any(c.startswith("GAP-") for c in cited))

    if exp["expected_verdict"] == "conclusive":
        leading = [h for h in hyps if h["status"] == "leading"]
        got = leading[0]["root_cause_category"] if leading else None
        _check(checks, "leading hypothesis category",
               got == exp["expected_root_cause_category"],
               f"expected {exp['expected_root_cause_category']}, got {got}")

    if key == "INC-1004":
        high = [h["id"] for h in hyps if h["confidence"] == "high"]
        _check(checks, "no confidence above medium", not high, f"high: {high}")

        words = exp["redis_hypothesis_keywords"]
        redis = [h for h in hyps if any(w in h["title"].lower() for w in words)]
        ruled_out = [h["id"] for h in redis if h["status"] == "ruled_out"]
        _check(checks, "Redis hypotheses not ruled out", not ruled_out,
               f"ruled out: {ruled_out}")
        result["warnings"] = [] if redis else [
            "No hypothesis mentions Redis; check that it was considered."]

        texts = [f"{c['description']} {c['why']}".lower() for c in inv["next_checks"]]
        matched = match_next_checks(texts, exp["expected_next_checks"])
        need = exp["min_next_checks_matched"]
        _check(checks, f"at least {need} expected next checks", len(matched) >= need,
               f"matched {len(matched)}: {matched}")
        result["limitations"] = KEYWORD_LIMITATION

    result["passed"] = all(c["passed"] for c in checks)
    return result


def latest_runs(runs_dir: Path) -> list[Path]:
    latest: dict[str, Path] = {}
    for p in sorted(runs_dir.glob("*_INC-*.json")):
        key = json.loads(p.read_text(encoding="utf-8")).get("incident_key")
        latest[key] = p                       # sorted by timestamp, so last wins
    return list(latest.values())


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or latest_runs(AGENT_ROOT / "runs")
    if not paths:
        print("No run files found in runs/.")
        return 1
    expectations = load_expectations()
    all_passed = True
    for p in paths:
        r = evaluate_run(json.loads(p.read_text(encoding="utf-8")), expectations)
        all_passed &= r["passed"]
        print(f"\n{r['incident_key']}  ({p.name})  {'PASS' if r['passed'] else 'FAIL'}")
        for c in r["checks"]:
            print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}"
                  + (f" - {c['detail']}" if c["detail"] else ""))
        for w in r.get("warnings", []):
            print(f"  [WARN] {w}")
        if r.get("limitations"):
            print(f"  note: {r['limitations']}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
