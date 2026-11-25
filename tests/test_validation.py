"""Offline tests for investigator/validation.py."""

import copy
import unittest

from investigator.validation import ValidationContext, validate_submission

CTX = ValidationContext(
    incident_key="INC-1004",
    evidence_kinds={"E-1": "observation", "E-2": "negative_finding", "E-3": "observation"},
    gap_ids={"GAP-9"},
    similar_keys={"INC-1002", "INC-1006"},
)


def ref(eid, reason="because"):
    return {"evidence_id": eid, "reason": reason}


def hyp(hid="H1", **kw):
    h = {"id": hid, "title": "Single bad pod", "root_cause_category": "infrastructure",
         "confidence": "medium", "status": "plausible",
         "supporting_evidence": [ref("E-1")], "contradicting_evidence": [],
         "related_history": ["INC-1006"]}
    h.update(kw)
    return h


def valid_inconclusive():
    return {
        "incident_key": "INC-1004", "verdict": "inconclusive", "summary": "Not enough data.",
        "hypotheses": [
            hyp("H1"),
            hyp("H2", title="Redis eviction", root_cause_category="resource_exhaustion",
                confidence="low", status="weakened", supporting_evidence=[],
                contradicting_evidence=[ref("E-3")], related_history=["INC-1002"]),
        ],
        "next_checks": [{"description": "Per-pod 5xx breakdown", "why": "Locate errors",
                         "hypotheses": ["H1"], "based_on_gaps": ["GAP-9"]}],
    }


def valid_conclusive():
    s = valid_inconclusive()
    s["verdict"] = "conclusive"
    s["hypotheses"][0].update(status="leading", confidence="high")
    s["next_checks"] = []
    return s


class ValidationTests(unittest.TestCase):
    def errors(self, sub):
        return validate_submission(sub, CTX)

    def assertRejected(self, sub, fragment):
        errs = self.errors(sub)
        self.assertTrue(any(fragment in e for e in errs), f"{fragment!r} not in {errs}")

    # --- valid -------------------------------------------------------------
    def test_valid_inconclusive(self):
        self.assertEqual(self.errors(valid_inconclusive()), [])

    def test_valid_conclusive(self):
        self.assertEqual(self.errors(valid_conclusive()), [])

    # --- structure and types --------------------------------------------------
    def test_not_an_object(self):
        self.assertRejected(["x"], "must be an object")

    def test_missing_and_unexpected_fields(self):
        s = valid_inconclusive()
        del s["summary"]
        s["confidence_score"] = 0.9
        self.assertRejected(s, "missing field(s) ['summary']")
        s = valid_inconclusive()
        s["hypotheses"][0]["probability"] = 0.8
        self.assertRejected(s, "unexpected field(s) ['probability']")
        s = valid_inconclusive()
        s["next_checks"][0]["owner"] = "me"
        self.assertRejected(s, "unexpected field(s) ['owner']")

    def test_wrong_enum_and_types(self):
        s = valid_inconclusive()
        s["verdict"] = "probably"
        self.assertRejected(s, "verdict: must be one of")
        s = valid_inconclusive()
        s["hypotheses"][0]["confidence"] = 0.7
        self.assertRejected(s, "confidence: must be one of")
        s = valid_inconclusive()
        s["hypotheses"][0]["root_cause_category"] = "aliens"
        self.assertRejected(s, "root_cause_category: must be one of")
        s = valid_inconclusive()
        s["hypotheses"] = {"H1": "x"}
        self.assertRejected(s, "hypotheses: must be a list")
        s = valid_inconclusive()
        s["summary"] = "   "
        self.assertRejected(s, "summary: must be a non-empty string")

    def test_wrong_incident_key(self):
        s = valid_inconclusive()
        s["incident_key"] = "INC-1002"
        self.assertRejected(s, "incident_key: must be 'INC-1004'")

    def test_duplicate_and_malformed_hypothesis_ids(self):
        s = valid_inconclusive()
        s["hypotheses"][1]["id"] = "H1"
        self.assertRejected(s, "duplicate hypothesis id H1")
        s = valid_inconclusive()
        s["hypotheses"][0]["id"] = ["H1"]
        self.assertRejected(s, "must look like H1")

    def test_unhashable_values_do_not_crash(self):
        s = valid_inconclusive()
        s["next_checks"][0]["hypotheses"] = [{"id": "H1"}]
        s["next_checks"][0]["based_on_gaps"] = [["GAP-9"]]
        s["hypotheses"][0]["related_history"] = [{"key": "INC-1006"}]
        s["hypotheses"][0]["supporting_evidence"] = [{"evidence_id": ["E-1"], "reason": "x"}]
        self.assertTrue(self.errors(s))

    # --- evidence ids ------------------------------------------------------------
    def test_gap_id_never_accepted_as_evidence(self):
        for field in ("supporting_evidence", "contradicting_evidence"):
            s = valid_inconclusive()
            s["hypotheses"][0][field] = [ref("GAP-9")]
            self.assertRejected(s, "GAP-9 is missing data")

    def test_unknown_evidence_id(self):
        s = valid_inconclusive()
        s["hypotheses"][0]["supporting_evidence"] = [ref("E-27")]
        self.assertRejected(s, "E-27 is not an evidence id returned by get_evidence")

    def test_duplicate_and_conflicting_evidence(self):
        s = valid_inconclusive()
        s["hypotheses"][0]["supporting_evidence"] = [ref("E-1"), ref("E-1")]
        self.assertRejected(s, "E-1 is listed twice")
        s = valid_inconclusive()
        s["hypotheses"][0]["contradicting_evidence"] = [ref("E-1")]
        self.assertRejected(s, "both supporting and contradicting")

    def test_gaps_only_allowed_in_next_checks(self):
        s = valid_inconclusive()
        s["next_checks"][0]["based_on_gaps"] = ["E-1"]
        self.assertRejected(s, "'E-1' is not a GAP id")

    # --- history ---------------------------------------------------------------------
    def test_history_must_come_from_find_similar(self):
        s = valid_inconclusive()
        s["hypotheses"][0]["related_history"] = ["INC-1007"]
        self.assertRejected(s, "INC-1007 was not returned by find_similar_incidents")
        s = valid_inconclusive()
        s["hypotheses"][0]["related_history"] = ["INC-1004"]
        self.assertRejected(s, "cannot reference the target incident")

    def test_history_alone_cannot_justify_confidence(self):
        s = valid_inconclusive()
        s["hypotheses"][0]["supporting_evidence"] = []
        self.assertRejected(s, "similar past incidents alone are not enough")

    # --- status rules ------------------------------------------------------------------
    def test_negative_finding_alone_cannot_rule_out(self):
        s = valid_inconclusive()
        s["hypotheses"][1].update(status="ruled_out", contradicting_evidence=[ref("E-2")])
        self.assertRejected(s, "ruled_out needs at least one contradicting evidence of kind 'observation'")

    def test_observation_can_rule_out(self):
        s = valid_inconclusive()
        s["hypotheses"][1].update(status="ruled_out", contradicting_evidence=[ref("E-2"), ref("E-3")])
        self.assertEqual(self.errors(s), [])

    def test_ruled_out_must_be_low_confidence(self):
        s = valid_inconclusive()
        s["hypotheses"][1].update(status="ruled_out", confidence="medium",
                                  supporting_evidence=[ref("E-1")])
        self.assertRejected(s, "ruled_out hypothesis must have confidence 'low'")

    def test_weakened_needs_contradiction(self):
        s = valid_inconclusive()
        s["hypotheses"][1]["contradicting_evidence"] = []
        self.assertRejected(s, "weakened needs at least one contradicting evidence")

    # --- verdict rules -------------------------------------------------------------------
    def test_conclusive_needs_exactly_one_high_leading(self):
        s = valid_conclusive()
        s["hypotheses"][0]["confidence"] = "medium"
        self.assertRejected(s, "needs confidence 'high'")
        s = valid_conclusive()
        s["hypotheses"][0]["status"] = "plausible"
        self.assertRejected(s, "exactly one 'leading' hypothesis is required")
        s = valid_conclusive()
        s["hypotheses"][1].update(status="leading", confidence="high",
                                  supporting_evidence=[ref("E-1")], contradicting_evidence=[])
        self.assertRejected(s, "at most one hypothesis may be 'leading'")

    def test_inconclusive_rules(self):
        s = valid_inconclusive()
        s["hypotheses"][0]["confidence"] = "high"
        self.assertRejected(s, "no hypothesis may have confidence 'high'")
        s = valid_inconclusive()
        s["next_checks"] = []
        self.assertRejected(s, "next_checks must not be empty")

    def test_next_check_must_reference_known_hypotheses(self):
        s = valid_inconclusive()
        s["next_checks"][0]["hypotheses"] = ["H7"]
        self.assertRejected(s, "unknown hypothesis id 'H7'")

    def test_validation_does_not_modify_input(self):
        s = valid_inconclusive()
        before = copy.deepcopy(s)
        self.errors(s)
        self.assertEqual(s, before)


if __name__ == "__main__":
    unittest.main()
