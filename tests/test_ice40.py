from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fpga_agent import ice40

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "ice40_pwm"


def _toolchain_ready() -> bool:
    tools = ice40.toolchain()
    return tools["reason"] is None


def _copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination, ignore=shutil.ignore_patterns("build", "reports"))
    return destination


class Ice40ParsingTests(unittest.TestCase):
    def test_parse_utilization_reads_percent_table(self):
        log = ("Info: Device utilisation:\n"
               "Info: \t ICESTORM_LC:      35/   1280     2%\n"
               "Info: \t ICESTORM_RAM:       0/     16     0%\n")
        resources = ice40._parse_utilization(log)
        self.assertEqual(resources["ICESTORM_LC"], {"used": 35, "available": 1280, "percent": 2})
        self.assertEqual(resources["ICESTORM_RAM"]["used"], 0)

    def test_parse_utilization_ignores_non_table_lines(self):
        self.assertEqual(ice40._parse_utilization("Info: Placed 10 cells based on constraints.\n"), {})

    def test_parse_timing_returns_last_clock(self):
        log = ("Info: Max frequency for clock 'clk$SB_IO_IN_$glb_clk': 326.90 MHz (PASS at 12.00 MHz)\n"
               "Info: Max frequency for clock 'clk$SB_IO_IN_$glb_clk': 365.36 MHz (PASS at 12.00 MHz)\n")
        timing = ice40._parse_timing(log)
        self.assertIsNotNone(timing)
        self.assertEqual(timing["clock"], "clk$SB_IO_IN_$glb_clk")
        self.assertAlmostEqual(timing["max_frequency_mhz"], 365.36)
        self.assertTrue(timing["pass"])

    def test_parse_timing_none_without_marker(self):
        self.assertIsNone(ice40._parse_timing("Info: routing complete\n"))

    def test_toolchain_reports_missing_tools(self):
        tools = ice40.toolchain()
        if tools["reason"]:
            self.assertTrue(tools["missing"])
            self.assertIn("openfpga", tools["reason"])
        else:
            self.assertIsNone(tools["reason"])
            for key in ("yosys", "nextpnr", "icepack"):
                self.assertTrue(tools[key], key)


class Ice40FixtureTests(unittest.TestCase):
    """The pinned fixture must implement to a real iCE40 bitstream when tools exist."""

    def setUp(self):
        if not _toolchain_ready():
            self.skipTest("Yosys + nextpnr-ice40 + icepack (openfpga extra) required")

    def test_full_flow_produces_bitstream(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            report = ice40.run(project, {"top": "pwm_breath", "sources": ["rtl/pwm_breath.v"],
                                         "pcf": "constraints/pins.pcf", "package": "tq144",
                                         "device": "iCE40HX1K", "nextpnr_device": "hx1k"},
                               timeout=600)
            self.assertEqual(report["status"], "pass", report["reason"])
            self.assertEqual(report["stages"]["synthesis"]["status"], "pass")
            self.assertEqual(report["stages"]["place_route"]["status"], "pass")
            self.assertEqual(report["stages"]["bitstream"]["status"], "pass")
            bitstream = project / report["stages"]["bitstream"]["artifact"]
            self.assertTrue(bitstream.is_file())
            self.assertGreater(bitstream.stat().st_size, 0)
            self.assertGreater(report["utilization"]["ICESTORM_LC"]["used"], 0)
            self.assertIsNotNone(report["timing"])

    def test_read_report_returns_empty_dict_when_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(ice40.read_report(project), {})


if __name__ == "__main__":
    unittest.main()
