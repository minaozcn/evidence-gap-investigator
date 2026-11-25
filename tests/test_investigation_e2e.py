"""End-to-end mock flow: scripted fake model + real tools + real (read-only) MySQL,
then scripts/evaluate.py against tests/fixtures/expected_outcomes.json.

No API calls. Skipped automatically when MySQL cannot be reached.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from dotenv import load_dotenv

AGENT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(AGENT_ROOT / ".env")

from investigator.db import connect  # noqa: E402
from investigator.investigation import run_investigation, save_run  # noqa: E402
from investigator.tools import execute_tool  # noqa: E402
from scripts import evaluate  # noqa: E402
from tests.fakes import (ScriptedProvider, evidence_id, gap_id,  # noqa: E402
                         gather_first_tools, latest_results, ref, submit)


def H(hid, title, category, confidence, status, support=(), contra=(), history=()):
    return {"id": hid, "title": title, "root_cause_category": category,
            "confidence": confidence, "status": status,
            "supporting_evidence": [ref(e) for e in support],
            "contradicting_evidence": [ref(e) for e in contra],
            "related_history": list(history)}


def check(desc, why, hyps, gaps=()):
    return {"description": desc, "why": why, "hypotheses": list(hyps), "based_on_gaps": list(gaps)}


# --- Scripted "good investigator" answers built from REAL tool output ----------

def answer_1003(r):
    e = lambda t: evidence_id(r, t)  # noqa: E731
    return {
        "incident_key": "INC-1003", "verdict": "conclusive",
        "summary": "Release v2.4.0 shrank the DB pool; rollback fixed it.",
        "hypotheses": [
            H("H1", "DB_POOL_MAX lowered by release v2.4.0", "config_change", "high", "leading",
              support=[e("DB_POOL_MAX 50 -> 10"), e("Timeout acquiring connection"),
                       e("Rolled back to v2.3.9")], history=["INC-1001"]),
            H("H2", "Payment provider outage", "third_party", "low", "weakened",
              contra=[e('No "upstream payment-gw timeout"')], history=["INC-1005"]),
            H("H3", "Database overload", "resource_exhaustion", "low", "ruled_out",
              contra=[e("db-primary CPU 22%")]),
        ],
        "next_checks": [],
    }


def answer_1004(r):
    e = lambda t: evidence_id(r, t)  # noqa: E731
    g = lambda s: gap_id(r, s)       # noqa: E731
    return {
        "incident_key": "INC-1004", "verdict": "inconclusive",
        "summary": "Client-side Redis latency without server-side confirmation; key data missing.",
        "hypotheses": [
            H("H1", "Problem on a single auth-service pod or node", "infrastructure", "medium",
              "plausible", support=[e("works on retry")], history=["INC-1006"]),
            H("H2", "Redis memory exhaustion / eviction", "resource_exhaustion", "low", "weakened",
              contra=[e("used_memory = 41%")], history=["INC-1002"]),
            H("H3", "Slow Redis server commands", "resource_exhaustion", "low", "weakened",
              support=[e("Client-side Redis call latency")], contra=[e("SLOWLOG is empty")]),
            H("H4", "Network or DNS problem between auth-service and Redis", "infrastructure",
              "low", "plausible", support=[e("Client-side Redis call latency")],
              contra=[e("not aware of any network issues")]),
        ],
        "next_checks": [
            check("Recover the delayed auth-service logs for 06:12-06:35 UTC",
                  "The log gap covers the incident window", ["H1", "H3"], [g("auth-service")]),
            check("Break down 5xx responses per pod and node", "Tests the single-pod hypothesis",
                  ["H1"]),
            check("Collect Redis INFO (evicted_keys) and server-side latency history",
                  "Server-side latency monitor was off", ["H2", "H3"], [g("redis-server")]),
            check("Check TCP retransmits and DNS resolver logs between auth-service and Redis",
                  "Separates network delay from Redis delay", ["H4"]),
        ],
    }


def answer_1008(r):
    e = lambda t: evidence_id(r, t)  # noqa: E731
    return {
        "incident_key": "INC-1008", "verdict": "conclusive",
        "summary": "Provider API key expired at 02:00:00 UTC.",
        "hypotheses": [
            H("H1", "Expired payment provider API key", "expired_credential", "high", "leading",
              support=[e("expires_at = 2026-10-15 02:00:00"), e("HTTP 401 Unauthorized"),
                       e("failure rate 0% until 02:00:00")]),
            H("H2", "Disk pressure on worker-2", "resource_exhaustion", "low", "ruled_out",
              contra=[e("HTTP 401 Unauthorized"), e("No disk-related errors")]),
        ],
        "next_checks": [],
    }


GOOD = {"INC-1003": answer_1003, "INC-1004": answer_1004, "INC-1008": answer_1008}


def two_turn_script(key, build):
    def play(turn, messages, tools):
        return gather_first_tools(key) if turn == 1 else submit(build(latest_results(messages)))
    return play


class E2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = connect()
        except Exception as e:
            raise unittest.SkipTest(f"MySQL not reachable ({type(e).__name__})")
        cls.expectations = evaluate.load_expectations()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def investigate(self, key, script):
        provider = ScriptedProvider(script)
        run = run_investigation(provider, lambda n, a: execute_tool(self.conn, n, a), key)
        return run, provider

    def test_good_runs_complete_and_pass_evaluation(self):
        paths = []
        for key, build in GOOD.items():
            with self.subTest(key=key):
                run, provider = self.investigate(key, two_turn_script(key, build))
                self.assertEqual(run["status"], "completed", run["failure"])
                self.assertEqual(provider.turns, 2)
                result = evaluate.evaluate_run(run, self.expectations)
                self.assertTrue(result["passed"], result["checks"])
                paths.append(str(save_run(run, self.tmp.name)))
        out = io.StringIO()
        with redirect_stdout(out):
            rc = evaluate.main(paths)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("keyword substrings", out.getvalue())   # limitation is reported

    def test_gap_citation_is_repaired_once(self):
        def play(turn, messages, tools):
            if turn == 1:
                return gather_first_tools("INC-1004")
            sub = answer_1004(latest_results(messages))
            if turn == 2:   # first try: cite the log gap as if it were evidence
                sub["hypotheses"][0]["supporting_evidence"].append(
                    ref(gap_id(latest_results(messages), "auth-service")))
            return submit(sub)
        run, provider = self.investigate("INC-1004", play)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["rejected_submissions"]), 1)
        self.assertEqual(provider.turns, 3)
        cited = json.dumps(run["investigation"]["hypotheses"])
        self.assertNotIn("GAP-", cited)

    def test_overconfident_1004_fails_validation(self):
        def play(turn, messages, tools):
            if turn == 1:
                return gather_first_tools("INC-1004")
            sub = answer_1004(latest_results(messages))
            sub["verdict"] = "conclusive"            # overstated, never fixed
            return submit(sub)
        run, _ = self.investigate("INC-1004", play)
        self.assertEqual(run["status"], "failed")
        self.assertIsNone(run["investigation"])
        result = evaluate.evaluate_run(run, self.expectations)
        self.assertFalse(result["passed"])

    def test_evaluation_catches_what_validation_allows(self):
        # Valid by the rules, but wrong for INC-1004: Redis eviction ruled out
        # from a single memory sample, and only one useful next check.
        def build(r):
            sub = answer_1004(r)
            sub["hypotheses"][1].update(status="ruled_out")
            sub["next_checks"] = sub["next_checks"][1:2]
            return sub
        run, _ = self.investigate("INC-1004", two_turn_script("INC-1004", build))
        self.assertEqual(run["status"], "completed")
        result = evaluate.evaluate_run(run, self.expectations)
        failed = {c["check"] for c in result["checks"] if not c["passed"]}
        self.assertIn("Redis hypotheses not ruled out", failed)
        self.assertIn("at least 2 expected next checks", failed)

    def test_evaluation_catches_misleading_history(self):
        # INC-1003 blamed on the payment provider because INC-1005 looked similar.
        def build(r):
            e = lambda t: evidence_id(r, t)  # noqa: E731
            return {"incident_key": "INC-1003", "verdict": "conclusive", "summary": "x",
                    "hypotheses": [H("H1", "Payment provider outage", "third_party", "high",
                                     "leading", support=[e("5xx rate 18%")],
                                     history=["INC-1005"])],
                    "next_checks": []}
        run, _ = self.investigate("INC-1003", two_turn_script("INC-1003", build))
        self.assertEqual(run["status"], "completed")
        result = evaluate.evaluate_run(run, self.expectations)
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
