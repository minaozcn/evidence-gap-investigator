"""Tests for investigator/tools.py.

Offline tests need nothing. Database tests are read-only (SELECT only) and
are skipped automatically when MySQL cannot be reached.
"""

import json
import unittest
from pathlib import Path

from dotenv import load_dotenv

AGENT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(AGENT_ROOT / ".env")

from investigator.db import connect  # noqa: E402
from investigator.tools import (GENERIC_TITLE_WORDS, TOOL_SPECS,  # noqa: E402
                                execute_tool, find_similar_incidents,
                                get_evidence, title_words)

ALL_KEYS = ["INC-1001", "INC-1002", "INC-1003", "INC-1004",
            "INC-1005", "INC-1006", "INC-1007", "INC-1008"]


# --- Offline ---------------------------------------------------------------------

class TitleWordsTests(unittest.TestCase):
    def test_generic_words_and_service_name_are_ignored(self):
        self.assertEqual(title_words("Intermittent login failures on auth-service",
                                     "auth-service"),
                         {"intermittent", "login"})

    def test_only_generic_words_gives_nothing(self):
        self.assertEqual(title_words("Service errors and failures", "x"), set())


class ExecuteToolTests(unittest.TestCase):
    def test_unknown_tool(self):
        self.assertIn("error", execute_tool(None, "drop_tables", {}))

    def test_wrong_arguments(self):
        for args in ({}, {"incident_key": 5}, {"incident_key": "INC-1", "sql": "x"}, "INC-1"):
            self.assertIn("error", execute_tool(None, "get_evidence", args), args)

    def test_specs_match_registry(self):
        self.assertEqual({s.name for s in TOOL_SPECS},
                         {"get_incident", "get_evidence", "find_similar_incidents"})


# --- Database (read-only) -----------------------------------------------------------

class DBTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = connect()
        except Exception as e:
            raise unittest.SkipTest(f"MySQL not reachable ({type(e).__name__})")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def query(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


class GetEvidenceTests(DBTestCase):
    def test_unknown_or_injected_key_returns_error(self):
        for key in ("INC-9999", "INC-1004' OR '1'='1"):
            self.assertIn("error", get_evidence(self.conn, key))

    def test_all_rows_returned_and_split_by_kind(self):
        for key in ALL_KEYS:
            out = get_evidence(self.conn, key)
            db = self.query("SELECT e.evidence_kind FROM evidence e JOIN incidents i "
                            "ON i.id = e.incident_id WHERE i.incident_key = %s", (key,))
            self.assertEqual(len(out["evidence"]) + len(out["data_gaps"]), len(db), key)
            self.assertEqual(len(out["data_gaps"]),
                             sum(r["evidence_kind"] == "data_gap" for r in db), key)
            self.assertFalse(out["truncated"])

    def test_data_gaps_are_never_evidence(self):
        out = get_evidence(self.conn, "INC-1004")
        self.assertEqual(len(out["data_gaps"]), 2)
        self.assertNotIn("data_gap", {e["kind"] for e in out["evidence"]})
        for gap in out["data_gaps"]:
            self.assertTrue(gap["gap_id"].startswith("GAP-"))
            self.assertNotIn("evidence_id", gap)
            self.assertIn("MISSING DATA", gap["note"])
            self.assertIsNotNone(gap["observed_until"])
        for e in out["evidence"]:
            self.assertTrue(e["evidence_id"].startswith("E-"))
            self.assertNotIn("gap_id", e)

    def test_evidence_is_ordered_by_observed_at(self):
        for key in ALL_KEYS:
            times = [e["observed_at"] for e in get_evidence(self.conn, key)["evidence"]]
            self.assertEqual(times, sorted(times), key)

    def test_fields_match_database_exactly(self):
        out = get_evidence(self.conn, "INC-1004")
        db = {f"E-{r['id']}": r for r in self.query(
            "SELECT e.* FROM evidence e JOIN incidents i ON i.id = e.incident_id "
            "WHERE i.incident_key = 'INC-1004'")}
        for e in out["evidence"]:
            row = db[e["evidence_id"]]
            self.assertEqual(e["content"], row["content"])
            self.assertEqual(e["limitations"], row["limitations"])
            self.assertEqual(e["reliability"], row["reliability"])
            self.assertEqual(e["observed_at"], row["observed_at"].isoformat() + "Z")
            self.assertEqual(e["collected_at"], row["collected_at"].isoformat() + "Z")
            expected_until = (row["observed_until"].isoformat() + "Z"
                              if row["observed_until"] else None)
            self.assertEqual(e["observed_until"], expected_until)

    def test_no_root_cause_in_output(self):
        for key in ALL_KEYS:
            self.assertNotIn("root_cause", json.dumps(get_evidence(self.conn, key)))


class FindSimilarIncidentsTests(DBTestCase):
    def setUp(self):
        self.status = {r["incident_key"]: r["status"]
                       for r in self.query("SELECT incident_key, status FROM incidents")}

    def keys(self, target):
        return [m["incident_key"]
                for m in find_similar_incidents(self.conn, target)["similar_incidents"]]

    def test_unknown_key_returns_error(self):
        self.assertIn("error", find_similar_incidents(self.conn, "INC-9999"))

    def test_target_never_appears_in_its_own_results(self):
        for key in ALL_KEYS:
            self.assertNotIn(key, self.keys(key))

    def test_only_resolved_incidents_are_returned(self):
        for key in ALL_KEYS:
            for k in self.keys(key):
                self.assertEqual(self.status[k], "resolved", f"{k} returned for {key}")

    def test_open_incidents_never_appear(self):
        open_keys = {k for k, s in self.status.items() if s != "resolved"}
        for key in ALL_KEYS:
            self.assertFalse(open_keys & set(self.keys(key)), key)

    def test_target_root_cause_is_not_leaked_for_resolved_target(self):
        own = self.query("SELECT root_cause FROM incidents "
                         "WHERE incident_key = 'INC-1001'")[0]["root_cause"]
        self.assertNotIn(own, json.dumps(find_similar_incidents(self.conn, "INC-1001")))

    def test_every_match_explains_why(self):
        for key in ALL_KEYS:
            out = find_similar_incidents(self.conn, key)
            self.assertTrue(out["similarity_method"])
            for m in out["similar_incidents"]:
                r = m["match_reasons"]
                self.assertTrue(r["same_service"] or r["shared_title_words"], m)
                self.assertFalse(set(r["shared_title_words"]) & GENERIC_TITLE_WORDS, m)

    def test_expected_scenarios(self):
        self.assertEqual(set(self.keys("INC-1003")), {"INC-1001", "INC-1005"})
        self.assertEqual(set(self.keys("INC-1004")), {"INC-1002", "INC-1006"})
        # payments-worker has no history; "failing" alone is too generic.
        self.assertEqual(self.keys("INC-1008"), [])


if __name__ == "__main__":
    unittest.main()
