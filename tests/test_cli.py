from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fpga_agent.cli import _read_brief, parser


class CliParserTests(unittest.TestCase):
    def test_tui_workspace_and_resume_options_parse(self):
        args = parser().parse_args(["tui", "--workspace", "work", "--resume", "session-1"])
        self.assertEqual(args.command, "tui")
        self.assertEqual(args.workspace, "work")
        self.assertEqual(args.resume, "session-1")

    def test_engineering_commands_require_project_and_expose_options(self):
        lint = parser().parse_args(["lint", "benchmarks/design"])
        verify = parser().parse_args(["verify", "benchmarks/design"])
        review = parser().parse_args(["review", "benchmarks/design", "--scope", "simulation"])
        self.assertEqual((lint.command, lint.project), ("lint", "benchmarks/design"))
        self.assertEqual((verify.command, verify.project), ("verify", "benchmarks/design"))
        self.assertEqual((review.command, review.scope), ("review", "simulation"))

    def test_run_pruning_defaults_to_dry_run_and_can_be_explicitly_applied(self):
        args = parser().parse_args(["prune-runs", "benchmarks/demo"])
        self.assertEqual(args.command, "prune-runs")
        self.assertEqual(args.keep, 5)
        self.assertFalse(args.apply)
        applied = parser().parse_args(["prune-runs", "benchmarks/demo", "--keep", "3", "--apply"])
        self.assertEqual(applied.keep, 3)
        self.assertTrue(applied.apply)

    def test_brief_file_and_project_default_are_supported(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / "spec").mkdir()
            (project / "spec/brief.md").write_text("default brief\n", encoding="utf-8")
            brief_file = project / "request.md"
            brief_file.write_text("file brief\n", encoding="utf-8")
            self.assertEqual(_read_brief(project, None, None), "default brief\n")
            self.assertEqual(_read_brief(project, None, str(brief_file)), "file brief\n")
            self.assertEqual(_read_brief(project, "inline", None), "inline")

    def test_brief_inputs_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "只能指定一个"):
            _read_brief(Path("."), "inline", "brief.md")


if __name__ == "__main__":
    unittest.main()
