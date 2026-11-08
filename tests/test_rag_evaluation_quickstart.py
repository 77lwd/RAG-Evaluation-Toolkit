"""The quickstart demonstrates metadata governance only, never a model run."""

import unittest

from examples.quickstart import run_quickstart


class QuickstartTests(unittest.TestCase):
    def test_synthetic_three_system_flow_freezes_without_authorizing_a_run(self):
        report = run_quickstart()

        self.assertEqual(report["systems"], 3)
        self.assertEqual(report["comparisons"], 2)
        self.assertEqual(report["readiness_status"], "complete_for_independent_review")
        self.assertEqual(report["freeze_status"], "valid_metadata_seal")
        self.assertTrue(report["demo_only"])
        self.assertFalse(report["run_authorized"])
        self.assertEqual(report["executed_models"], 0)


if __name__ == "__main__":
    unittest.main()
