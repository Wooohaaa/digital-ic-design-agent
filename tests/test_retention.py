from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fpga_agent.retention import prune_td_runs


class TdRunRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        root = self.project / "td/runs"
        for index in range(8):
            run = root / f"run_{index:04d}"
            run.mkdir(parents=True)
            (run / "build.log").write_text(f"run {index}\n", encoding="utf-8")
            run.touch()
            (run / "build.log").touch()
            import os
            os.utime(run, (1_000 + index, 1_000 + index))
        reports = self.project / "reports"
        reports.mkdir()
        (reports / "td.json").write_text(json.dumps({"run_directory": "td/runs/run_0000"}), encoding="utf-8")
        (reports / "hardware-video.json").write_text('"source_run": "run_0001"', encoding="utf-8")

    def test_dry_run_keeps_recent_and_report_referenced_runs(self):
        result = prune_td_runs(self.project, keep=2)
        self.assertFalse(result["apply"])
        self.assertEqual(set(result["preserved"]), {"run_0000", "run_0001", "run_0006", "run_0007"})
        self.assertEqual(set(result["candidates"]), {"run_0002", "run_0003", "run_0004", "run_0005"})
        self.assertTrue((self.project / "td/runs/run_0002").is_dir())

    def test_apply_only_removes_unreferenced_runs_outside_keep_window(self):
        result = prune_td_runs(self.project, keep=2, apply=True)
        self.assertEqual(set(result["removed"]), {"run_0002", "run_0003", "run_0004", "run_0005"})
        for name in ("run_0000", "run_0001", "run_0006", "run_0007"):
            self.assertTrue((self.project / "td/runs" / name).is_dir())
        for name in result["removed"]:
            self.assertFalse((self.project / "td/runs" / name).exists())

    def test_keep_must_be_positive(self):
        for keep in (0, -1, True):
            with self.assertRaises(ValueError):
                prune_td_runs(self.project, keep=keep)


if __name__ == "__main__":
    unittest.main()
