from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fpga_agent.cpu_bench import trusted_hashes
from fpga_agent.evidence import implementation_valid, input_hashes, simulation_valid, source_hashes, sha


class EvidenceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        for folder in ("rtl", "tb", "constraints", "board", "reports"):
            (self.project / folder).mkdir(parents=True, exist_ok=True)
        for path, content in {
            "rtl/core.v": "module core; endmodule\n",
            "tb/tb.v": "module tb; endmodule\n",
            "constraints/pins.adc": "pin A\n",
            "constraints/clock.sdc": "clock 48MHz\n",
            "board/hx1p35a.json": '{"board":"HX1P35A"}\n',
            "reports/demo.bit": "valid-bitstream",
        }.items():
            (self.project / path).write_text(content, encoding="utf-8")
        self.manifest = {
            "rtl_sources": ["rtl/core.v"],
            "testbench_sources": ["tb/tb.v"],
            "board_constraints": "constraints/pins.adc",
            "timing_constraints": "constraints/clock.sdc",
            "device_family": "ph1",
            "device": "PH1P35MDG324",
            "top_module": "core",
            "testbench_top": "tb",
            "board_io": {"clock": "A"},
            "verification": {"kind": "riscv-retire", "seeds": 4},
            "board_signature": "1234abcd",
        }
        self.simulation = {
            "status": "pass",
            "verification_kind": "riscv-retire",
            "source_hashes": source_hashes(self.project, self.manifest),
            "trusted_harness_hashes": trusted_hashes(self.project),
            "board_soc": "pass",
            "seeds": 4,
        }
        self.implementation = {
            "status": "pass",
            "stage": "full",
            "input_hashes": input_hashes(self.project, self.manifest),
            "timing_summary": {"setup_wns_ns": 0.1, "hold_wns_ns": 0.05},
            "bitstream_hashes": {"reports/demo.bit": sha(self.project / "reports/demo.bit")},
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_rejects_constraints_changed_after_full_implementation(self) -> None:
        self.assertEqual(implementation_valid(self.project, self.manifest, self.implementation), (True, "pass"))
        (self.project / "constraints/pins.adc").write_text("pin B\n", encoding="utf-8")
        valid, reason = implementation_valid(self.project, self.manifest, self.implementation)
        self.assertFalse(valid)
        self.assertIn("changed", reason)

    def test_rejects_forged_self_pass_cpu_benchmark_report(self) -> None:
        forged = dict(self.simulation)
        forged["trusted_harness_hashes"] = {"riscv.py": "forged"}
        valid, reason = simulation_valid(self.project, self.manifest, forged)
        self.assertFalse(valid)
        self.assertIn("reference/harness changed", reason)

    def test_rejects_modified_bitstream_after_td_report(self) -> None:
        self.assertEqual(implementation_valid(self.project, self.manifest, self.implementation), (True, "pass"))
        (self.project / "reports/demo.bit").write_text("changed-bitstream", encoding="utf-8")
        valid, reason = implementation_valid(self.project, self.manifest, self.implementation)
        self.assertFalse(valid)
        self.assertIn("missing or modified", reason)

    def test_testbench_change_requires_new_simulation_but_not_new_synthesis(self) -> None:
        (self.project/'tb/tb.v').write_text('changed verification')
        self.assertFalse(simulation_valid(self.project,self.manifest,self.simulation)[0])
        self.assertTrue(implementation_valid(self.project,self.manifest,self.implementation)[0])

    def test_rejects_mutated_td_run_source_snapshot(self) -> None:
        run_dir = self.project / "td" / "runs" / "run_snapshot"
        source_files = [
            "rtl/core.v",
            self.manifest["board_constraints"],
            self.manifest["timing_constraints"],
        ]
        for name in source_files:
            snapshot = run_dir / "sources" / name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes((self.project / name).read_bytes())
        report = dict(self.implementation, run_directory="td/runs/run_snapshot")
        self.assertEqual(implementation_valid(self.project, self.manifest, report), (True, "pass"))

        (run_dir / "sources" / "rtl" / "core.v").write_text("module core; wire stale; endmodule\n", encoding="utf-8")
        valid, reason = implementation_valid(self.project, self.manifest, report)
        self.assertFalse(valid)
        self.assertIn("snapshot", reason)


if __name__ == "__main__":
    unittest.main()
