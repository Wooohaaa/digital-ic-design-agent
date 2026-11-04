from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fpga_agent.td import _tcl_braced, build_tcl, find_td_command, run_td


class TdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        for folder in ("rtl", "constraints"):
            (self.project / folder).mkdir(parents=True, exist_ok=True)
        (self.project / "rtl" / "core.v").write_text("module core; endmodule\n", encoding="utf-8")
        (self.project / "constraints" / "pins.adc").write_text("pin data\n", encoding="utf-8")
        (self.project / "constraints" / "clock.sdc").write_text("clock data\n", encoding="utf-8")
        manifest = {
            "project": "demo",
            "device_family": "ph1",
            "device": "PH1P35MDG324",
            "top_module": "core",
            "rtl_sources": ["rtl/core.v"],
            "board_constraints": "constraints/pins.adc",
            "timing_constraints": "constraints/clock.sdc",
        }
        (self.project / "project.json").write_text(json.dumps(manifest), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_tcl_word_quotes_windows_paths_and_literal_braces(self) -> None:
        quoted = _tcl_braced(r'C:\work\{rtl}\file $name "quoted"')
        self.assertTrue(quoted.startswith('"') and quoted.endswith('"'))
        self.assertIn(r"\\work", quoted)
        self.assertIn(r"\{rtl}", quoted)
        self.assertIn(r"\$name", quoted)
        self.assertIn(r'\"quoted\"', quoted)

    def test_td_command_does_not_fall_back_to_gui_launcher(self) -> None:
        fake_root = self.root / "td"
        (fake_root / "bin").mkdir(parents=True)
        (fake_root / "bin" / "td.exe").write_text("", encoding="utf-8")
        with patch("fpga_agent.td.shutil.which", return_value=None):
            with self.assertRaises(FileNotFoundError):
                find_td_command(str(fake_root))

    def test_build_tcl_uses_microsecond_run_directories_and_safe_words(self) -> None:
        first_tcl, first_run = build_tcl(self.project, "synth")
        second_tcl, second_run = build_tcl(self.project, "synth")
        self.assertNotEqual(first_run, second_run)
        self.assertTrue(first_run.name.count("_") >= 3)
        tcl_text = first_tcl.read_text(encoding="utf-8")
        self.assertIn('set project_dir "', tcl_text)
        self.assertIn("add_files \"sources/rtl/core.v\"", tcl_text)

    def _run_full_with_report(self, report: str, bitstream: bool = True):
        executable = self.root / "td_commands_prompt.exe"
        executable.write_text("", encoding="utf-8")
        output = "Run syn_1 success\nRun phy_1 success\nFPGA_AGENT_TD_FLOW_DONE\n"

        def fake_run(command, **kwargs):
            run_dir = Path(kwargs["cwd"])
            runs = run_dir / "demo_full_Runs" / "phy_1"
            runs.mkdir(parents=True, exist_ok=True)
            (runs / "final_timing.rpt").write_text(report, encoding="utf-8")
            if bitstream:
                (runs / "demo.bit").write_bytes(b"bitstream")
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        with patch("fpga_agent.td.find_td_command", return_value=executable), patch(
            "fpga_agent.td.subprocess.run", side_effect=fake_run
        ):
            return run_td(self.project, "full")

    def test_full_flow_requires_bitstream_explicitly(self) -> None:
        code, output, _, _ = self._run_full_with_report("SWNS: 0.1ns HWNS: 0.2ns", bitstream=False)
        self.assertEqual(code, 1)
        self.assertIn("未生成任何 .bit", output)

    def test_full_flow_rejects_nan_and_infinite_timing(self) -> None:
        code, output, _, _ = self._run_full_with_report("SWNS: NaNns HWNS: Infinityns")
        self.assertEqual(code, 1)
        self.assertIn("非有限", output)

    def test_td_timeout_bytes_and_failed_flow_are_reported(self) -> None:
        executable = self.root / "td_commands_prompt.exe"
        executable.write_text("", encoding="utf-8")
        with patch("fpga_agent.td.find_td_command", return_value=executable), patch(
            "fpga_agent.td.subprocess.run",
            side_effect=subprocess.TimeoutExpired("td", 1, output=b"partial", stderr=b"bad"),
        ):
            code, output, _, _ = run_td(self.project, "synth", timeout=1)
        self.assertEqual(code, 124)
        self.assertIn("partial", output)
        self.assertIn("bad", output)

        with patch("fpga_agent.td.find_td_command", return_value=executable), patch(
            "fpga_agent.td.subprocess.run",
            return_value=SimpleNamespace(returncode=2, stdout="TD error", stderr=""),
        ):
            code, output, _, _ = run_td(self.project, "synth")
        self.assertEqual(code, 2)
        self.assertIn("TD error", output)
        self.assertIn("重放", output)


if __name__ == "__main__":
    unittest.main()
