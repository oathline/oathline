"""examples/stop_the_delete.py runs as printed: the agent's own yes and a yes before the showing are refused, a yes to an action that
changed after it was shown is refused, the stored action runs once on a real yes, the same yes again is refused, the chain verifies."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "stop_the_delete.py")


class StopTheDeleteTest(unittest.TestCase):
    def test_the_example_is_short_and_prints_the_refusals_the_one_run_and_a_verified_chain(self):
        with open(EXAMPLE, encoding="utf-8") as fh:
            self.assertLessEqual(len(fh.read().splitlines()), 60)
        run = subprocess.run([sys.executable, EXAMPLE], capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = run.stdout.replace("\r\n", "\n")
        lines = [ln.strip() for ln in out.splitlines()]
        self.assertIn("-> proposed | store: ['prod-db', 'prod-db-backups']", lines)
        self.assertIn("-> non_human_principal", lines)                                   # the agent's own yes
        self.assertEqual(lines.count("-> unheard"), 2)                                    # before the showing, and before the corrected showing
        self.assertIn("-> shown: True | heard: True", lines)
        self.assertIn("-> proposed | a yes on the old, withdrawn card: expired", lines)   # the action changed: the earlier yes does not carry
        self.assertIn("-> executed {'dropped': 'prod-db-backups', 'was': '3 snapshots'} | store: ['prod-db']", lines)
        self.assertIn("-> already_used", lines)                                           # the same yes again
        self.assertEqual(out.count("EXECUTED "), 1)                                       # one run in the chain
        self.assertEqual(out.count("EXECUTING "), 1)
        self.assertIn("WITHDRAWN            console", out)
        self.assertTrue(out.rstrip().endswith("chain ok: True"))


if __name__ == "__main__":
    unittest.main()
