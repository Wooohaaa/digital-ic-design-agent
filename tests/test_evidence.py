from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fpga_agent.cpu_bench import trusted_hashes
from fpga_agent.evidence import EVIDENCE_VERSION, implementation_valid, input_hashes, simulation_input_hashes, simulation_valid, source_hashes, sha, trusted_testbench_valid
from fpga_agent.riscv import corpus, run_reference


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
            'project':'demo',
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
            'evidence_version':EVIDENCE_VERSION,
            'verification_inputs':simulation_input_hashes(self.project,self.manifest),
            'returncode':0,
            'case_count':len(corpus(4)),
            'cases':[{'name':case.name,'status':'pass'} for case in corpus(4)],
            'instruction_coverage':sorted(set().union(*(run_reference(case).coverage for case in corpus(0)))),
        }
        self.implementation = {
            "status": "pass",
            "stage": "full",
            "input_hashes": input_hashes(self.project, self.manifest),
            "timing_summary": {"setup_wns_ns": 0.1, "hold_wns_ns": 0.05, 'setup_tns_ns':0,'hold_tns_ns':0},
            "bitstream_hashes": {"reports/demo.bit": sha(self.project / "reports/demo.bit")},
            'bitstreams':['reports/demo.bit'],
            'run_directory':'td/runs/run_snapshot',
        }
        run=self.project/self.implementation['run_directory']
        for name in self.manifest['rtl_sources']+[self.manifest['board_constraints'],self.manifest['timing_constraints']]:
            snapshot=run/'sources'/name
            snapshot.parent.mkdir(parents=True,exist_ok=True)
            snapshot.write_bytes((self.project/name).read_bytes())
        timing=run/'demo_full_Runs/phy_1/final_timing.rpt'
        timing.parent.mkdir(parents=True)
        timing.write_text('SWNS: 0.1ns STNS: 0ns HWNS: 0.05ns HTNS: 0ns')

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_changed_testbench_top_invalidates_old_pass(self):
        self.manifest['testbench_top']='another_tb'
        valid,reason=simulation_valid(self.project,self.manifest,self.simulation)
        self.assertFalse(valid)
        self.assertIn('configuration',reason)

    def test_rtl_compilation_order_is_part_of_implementation_evidence(self):
        (self.project/'rtl/other.v').write_text('module other; endmodule')
        self.manifest['rtl_sources'].append('rtl/other.v')
        before=input_hashes(self.project,self.manifest)
        self.manifest['rtl_sources'].reverse()
        self.assertNotEqual(before,input_hashes(self.project,self.manifest))

    def test_incomplete_or_failed_cpu_case_cannot_claim_pass(self):
        for broken in (
            dict(self.simulation,cases=self.simulation['cases'][:-1]),
            dict(self.simulation,case_count=0),
            dict(self.simulation,instruction_coverage=[]),
            dict(self.simulation,returncode=1),
        ):
            with self.subTest(report=broken):
                self.assertFalse(simulation_valid(self.project,self.manifest,broken)[0])

    def test_include_and_memory_image_changes_invalidate_evidence(self):
        (self.project/'rtl/defs.vh').write_text('`define W 8\n')
        (self.project/'image.hex').write_text('00112233\n')
        (self.project/'rtl/core.v').write_text('`include "defs.vh"\nmodule core; reg [31:0] rom[0:0]; initial $readmemh("image.hex",rom); endmodule\n')
        original=simulation_input_hashes(self.project,self.manifest)
        self.assertIn('rtl/defs.vh',original)
        self.assertIn('image.hex',original)
        (self.project/'image.hex').write_text('aabbccdd\n')
        self.assertNotEqual(original,simulation_input_hashes(self.project,self.manifest))

    def test_missing_or_negative_tns_blocks_implementation(self):
        for value in (None,-0.01,float('nan')):
            report=dict(self.implementation,timing_summary=dict(self.implementation['timing_summary'],setup_tns_ns=value))
            self.assertFalse(implementation_valid(self.project,self.manifest,report)[0])

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

    def trusted_hdl_manifest(self):
        bench = self.project / 'tb/tb.v'
        self.manifest['verification'] = {'kind': 'trusted-testbench', 'trusted_files': ['tb/tb.v']}
        self.manifest['trusted_testbench_hashes'] = {'tb/tb.v': sha(bench)}
        return self.manifest

    def test_trusted_testbench_requires_exact_pinned_inventory(self):
        manifest = self.trusted_hdl_manifest()
        self.assertEqual(trusted_testbench_valid(self.project, manifest),
                         (True, 'operator-owned testbench hashes match the manifest'))

        manifest['trusted_testbench_hashes']['tb/tb.v'] = '0' * 64
        valid, reason = trusted_testbench_valid(self.project, manifest)
        self.assertFalse(valid)
        self.assertIn('changed since its hash was pinned', reason)

    def test_trusted_testbench_rejects_incomplete_duplicate_and_unsafe_inventory(self):
        manifest = self.trusted_hdl_manifest()
        manifest['trusted_testbench_hashes'] = {}
        self.assertIn('inventory is incomplete', trusted_testbench_valid(self.project, manifest)[1])

        manifest = self.trusted_hdl_manifest()
        manifest['testbench_sources'] = ['tb/tb.v', 'tb/tb.v']
        self.assertIn('duplicate', trusted_testbench_valid(self.project, manifest)[1])

        manifest = self.trusted_hdl_manifest()
        manifest['testbench_sources'] = [['tb/tb.v']]
        self.assertIn('relative file paths', trusted_testbench_valid(self.project, manifest)[1])

        manifest = self.trusted_hdl_manifest()
        manifest['testbench_sources'] = ['../outside.v']
        manifest['verification']['trusted_files'] = ['../outside.v']
        manifest['trusted_testbench_hashes'] = {'../outside.v': '0' * 64}
        self.assertIn('missing or unsafe', trusted_testbench_valid(self.project, manifest)[1])

    def test_trusted_testbench_acceptance_is_bound_to_its_hashes(self):
        manifest = self.trusted_hdl_manifest()
        report = {
            'status': 'pass',
            'source_hashes': source_hashes(self.project, manifest),
            'evidence_version': EVIDENCE_VERSION,
            'verification_inputs': simulation_input_hashes(self.project, manifest),
            'returncode': 0,
        }
        self.assertEqual(simulation_valid(self.project, manifest, report), (True, 'pass'))
        (self.project / 'tb/tb.v').write_text('modified by a worker')
        self.assertFalse(simulation_valid(self.project, manifest, report)[0])


if __name__ == "__main__":
    unittest.main()
