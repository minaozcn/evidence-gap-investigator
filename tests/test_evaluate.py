"""Offline tests for the next-check matching in scripts/evaluate.py."""

import unittest

from scripts import evaluate

EXPECTED = evaluate.load_expectations()["INC-1004"]["expected_next_checks"]
POD = "per-pod / per-node 5xx distribution for auth-service"

# Next checks proposed by gemini-3.8-flash in the first real INC-1004 run.
REAL_RUN_1004 = [
    "Recover auth-service logs for the 06:12 - 06:35 window from host disks/local buffers "
    "to inspect specific 5xx stack traces and Redis connection errors.",
    "Enable Redis latency monitoring (latency-monitor-threshold > 0) and inspect network "
    "packet metrics/drops between auth pods and Redis.",
]


def match(texts):
    return evaluate.match_next_checks([t.lower() for t in texts], EXPECTED)


class NextCheckMatchingTests(unittest.TestCase):
    def test_mentioning_pods_is_not_a_per_pod_breakdown(self):
        # Regression: this real run was previously counted as 4/4.
        matched = match(REAL_RUN_1004)
        self.assertNotIn(POD, matched)
        self.assertEqual(len(matched), 2)

    def test_real_per_pod_breakdown_matches(self):
        for text in ("Break down 5xx responses per pod and node",
                     "Check the 5xx distribution across auth-service backends",
                     "Find out which pod returned the errors"):
            self.assertIn(POD, match([text]), text)

    def test_one_check_counts_for_at_most_one_expected_item(self):
        combined = ("Recover missing auth-service logs, collect Redis latency history and "
                    "check DNS, plus a per-pod breakdown")
        self.assertEqual(len(match([combined])), 1)

    def test_matching_is_maximal(self):
        # The Redis/network check could serve two items; the matcher must still
        # find an assignment that covers both checks.
        texts = ["Collect Redis server-side latency and network packet drops",
                 "Check DNS resolver logs between auth-service and Redis"]
        self.assertEqual(len(match(texts)), 2)


if __name__ == "__main__":
    unittest.main()
