"""Historical launchers cannot start retired engines or create new evidence."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RetirementTests(unittest.TestCase):
    def test_execution_entrypoints_fail_before_creating_output(self):
        for relative in (
            "engine_eval/run.py", "engine_eval/runtime.py", "engine_eval/workloads.py", "engine_eval/faults.py",
            "code_graph_eval/quality_run.py", "code_graph_eval/overlap.py", "code_graph_eval/overlap_workload.py",
        ):
            with self.subTest(script=relative), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "new-evidence"
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / relative), "--output", str(output)],
                    cwd=directory, capture_output=True, text=True, timeout=10, check=False,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("retired", result.stderr)
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
