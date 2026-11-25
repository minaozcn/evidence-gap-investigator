"""Integration tests against the local MySQL database (read-only).

Skipped automatically when the database cannot be reached. Every query here
is a SELECT or SHOW; nothing is written or deleted.
"""

import json
import os
import unittest
from pathlib import Path

from dotenv import load_dotenv

AGENT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(AGENT_ROOT / ".env")

from investigator.db import connect  # noqa: E402
from investigator.tools import get_incident  # noqa: E402

FIXTURE = json.loads((AGENT_ROOT / "tests" / "fixtures" / "expected_outcomes.json")
                     .read_text(encoding="utf-8"))["seed"]


def setUpModule():
    global conn
    try:
        conn = connect()
    except Exception as e:
        raise unittest.SkipTest(f"MySQL not reachable ({type(e).__name__}); "
                                "database tests skipped")


def tearDownModule():
    conn.close()


def query(sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


class ConnectionTests(unittest.TestCase):
    def test_session_uses_utc(self):
        self.assertEqual(query("SELECT @@session.time_zone AS tz")[0]["tz"], "+00:00")

    def test_app_user_is_not_root(self):
        user = query("SELECT CURRENT_USER() AS u")[0]["u"]
        self.assertFalse(user.startswith("root@"), user)

    def test_app_user_only_has_select_on_project_db(self):
        grants = [list(r.values())[0] for r in query("SHOW GRANTS")]
        db = os.environ["MYSQL_DATABASE"]
        for g in grants:
            if g.startswith("GRANT USAGE ON *.*"):
                continue          # "USAGE" means no privileges
            self.assertTrue(g.startswith(f"GRANT SELECT ON `{db}`.*"), g)


class SeedDataTests(unittest.TestCase):
    def test_incident_count(self):
        n = query("SELECT COUNT(*) AS n FROM incidents")[0]["n"]
        self.assertEqual(n, FIXTURE["incident_count"])

    def test_evidence_per_incident(self):
        rows = query("SELECT i.incident_key, COUNT(e.id) AS n FROM incidents i "
                     "LEFT JOIN evidence e ON e.incident_id = i.id GROUP BY i.id")
        counts = {r["incident_key"]: r["n"] for r in rows}
        expected = dict(FIXTURE["evidence_per_incident"])
        if counts.get("INC-1004") == expected["INC-1004"] + FIXTURE["followup_1004_adds"]:
            expected["INC-1004"] = counts["INC-1004"]   # followup_1004.sql was applied
        self.assertEqual(counts, expected)

    def test_no_orphan_evidence(self):
        n = query("SELECT COUNT(*) AS n FROM evidence e "
                  "LEFT JOIN incidents i ON i.id = e.incident_id WHERE i.id IS NULL")[0]["n"]
        self.assertEqual(n, 0)

    def test_foreign_key_exists(self):
        rows = query("SELECT REFERENCED_TABLE_NAME AS t FROM information_schema.KEY_COLUMN_USAGE "
                     "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'evidence' "
                     "AND COLUMN_NAME = 'incident_id' AND REFERENCED_TABLE_NAME IS NOT NULL")
        self.assertEqual([r["t"] for r in rows], ["incidents"])

    def test_open_incidents_have_no_root_cause(self):
        rows = query("SELECT incident_key FROM incidents WHERE status <> 'resolved' "
                     "AND (root_cause IS NOT NULL OR root_cause_category IS NOT NULL)")
        self.assertEqual(rows, ())

    def test_open_incidents_match_fixture(self):
        rows = query("SELECT incident_key FROM incidents WHERE status <> 'resolved' "
                     "ORDER BY incident_key")
        self.assertEqual([r["incident_key"] for r in rows], FIXTURE["open_incidents"])

    def test_windowed_evidence_has_an_end(self):
        n = query("SELECT COUNT(*) AS n FROM evidence WHERE evidence_kind <> 'observation' "
                  "AND observed_until IS NULL")[0]["n"]
        self.assertEqual(n, 0)

    def test_inc_1004_scenario(self):
        rows = query("SELECT e.evidence_kind, e.source_name, e.limitations FROM evidence e "
                     "JOIN incidents i ON i.id = e.incident_id WHERE i.incident_key = 'INC-1004'")
        gaps = {r["source_name"] for r in rows if r["evidence_kind"] == "data_gap"}
        self.assertEqual(gaps, {"auth-service", "redis-server"})
        slowlog = [r for r in rows if r["source_name"] == "redis-server"
                   and r["evidence_kind"] == "observation"]
        # Both Redis observations must carry a limitation (they weaken, not prove).
        self.assertTrue(slowlog and all(r["limitations"] for r in slowlog))

    def test_inc_1008_has_distractor_and_negative_findings(self):
        rows = query("SELECT e.evidence_kind, e.source_name FROM evidence e "
                     "JOIN incidents i ON i.id = e.incident_id WHERE i.incident_key = 'INC-1008'")
        kinds = [(r["evidence_kind"], r["source_name"]) for r in rows]
        self.assertIn(("observation", "node-exporter"), kinds)
        self.assertIn(("negative_finding", "jenkins"), kinds)


class GetIncidentToolTests(unittest.TestCase):
    def test_returns_the_right_record(self):
        r = get_incident(conn, "INC-1003")
        self.assertEqual(r["incident_key"], "INC-1003")
        self.assertEqual(r["title"], "checkout-api returning 502 errors")
        self.assertTrue(r["started_at"].endswith("Z"))

    def test_never_returns_root_cause(self):
        for key in ("INC-1003", "INC-1001"):      # open and resolved
            r = get_incident(conn, key)
            self.assertNotIn("root_cause", r)
            self.assertNotIn("root_cause_category", r)

    def test_unknown_key_returns_error(self):
        self.assertIn("error", get_incident(conn, "INC-9999"))

    def test_injection_attempt_is_treated_as_plain_text(self):
        self.assertIn("error", get_incident(conn, "INC-1003' OR '1'='1"))


if __name__ == "__main__":
    unittest.main()
