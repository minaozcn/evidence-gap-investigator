"""Offline tests: secrets are redacted, and the agent code cannot see the answers."""

import os
import unittest
from pathlib import Path
from unittest import mock

from investigator.redact import redact

AGENT_ROOT = Path(__file__).resolve().parents[1]


class RedactTests(unittest.TestCase):
    def test_secret_values_are_removed(self):
        env = {"GEMINI_API_KEY": "key-123-abc", "MYSQL_PASSWORD": "pw-456"}
        with mock.patch.dict(os.environ, env):
            out = redact("failed with key-123-abc and password pw-456")
        self.assertNotIn("key-123-abc", out)
        self.assertNotIn("pw-456", out)
        self.assertIn("<GEMINI_API_KEY redacted>", out)

    def test_missing_secrets_do_not_break_redaction(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(redact("hello"), "hello")


class AnswerIsolationTests(unittest.TestCase):
    def test_only_evaluate_script_references_expected_outcomes(self):
        for path in (AGENT_ROOT / "scripts").glob("*.py"):
            if path.name == "evaluate.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("expected_outcomes", text, path)
            self.assertNotIn("fixtures", text, path)

    def test_agent_code_never_references_expected_outcomes(self):
        for path in (AGENT_ROOT / "investigator").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in ("expected_outcomes", "tests/fixtures", "tests\\fixtures"):
                self.assertNotIn(needle, text, f"{path} references {needle}")

    def _function_sql(self, name):
        source = (AGENT_ROOT / "investigator" / "tools.py").read_text(encoding="utf-8")
        start = source.index(f"def {name}(")
        end = source.index("\ndef ", start + 1)
        return source[start:end].split("cur.execute(")[1].split(")")[0]

    def test_get_incident_query_does_not_select_root_cause(self):
        self.assertNotIn("root_cause", self._function_sql("get_incident"))

    def test_get_evidence_query_does_not_select_root_cause(self):
        self.assertNotIn("root_cause", self._function_sql("get_evidence"))

    def test_similar_query_is_limited_to_other_resolved_incidents(self):
        sql = self._function_sql("find_similar_incidents")
        self.assertIn("status = 'resolved'", sql)
        self.assertIn("id <> %s", sql)


if __name__ == "__main__":
    unittest.main()
