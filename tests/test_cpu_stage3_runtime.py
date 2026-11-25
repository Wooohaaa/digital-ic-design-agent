import json
from pathlib import Path

import pytest

from fpga_agent.cpu_rv64 import base_cases, branch, machine_cases, memory
from fpga_agent.lec import lec_inputs
from fpga_agent.sv39_accept import fixtures


def test_runtime_cases_halt_or_trap_at_distinct_sites():
    cases = {case.name: case for case in base_cases(1)}
    assert cases["fetch-fault"].fault_pc == 16
    trapped = cases["unaligned-store-3"]
    assert trapped.words[trapped.fault_pc // 4] == memory(3, 10, 11, 1, store=True)
    assert trapped.words[trapped.fault_pc // 4 + 1] == memory(3, 10, 11, 16, store=True)
    assert branch(1, 1, 0, -8) == 0xFE009CE3
    assert all(trace.count("T 0000000000000040") == 1 for _, trace in machine_cases())
    names, _, expected = fixtures()
    assert len(names) == 28
    assert sum(line.startswith("D ") for line in expected) == len(names)


def test_lec_top_parameter_values_are_bound_to_proof_inputs(tmp_path: Path):
    rtl = tmp_path / "core.v"
    rtl.write_text("module core #(parameter WIDTH=8) (); endmodule", encoding="ascii")
    manifest = {"top_module": "core", "rtl_sources": [rtl.name], "testbench_sources": [],
                "formal_equivalence": {"top_parameters": {"WIDTH": 8}}}
    path = tmp_path / "project.json"
    path.write_text(json.dumps(manifest), encoding="ascii")
    baseline = lec_inputs(tmp_path)
    manifest["formal_equivalence"]["top_parameters"]["WIDTH"] = 16
    path.write_text(json.dumps(manifest), encoding="ascii")
    assert lec_inputs(tmp_path)["@config"] != baseline["@config"]
    manifest["formal_equivalence"]["top_parameters"]["WIDTH"] = "16"
    path.write_text(json.dumps(manifest), encoding="ascii")
    with pytest.raises(ValueError, match="top_parameters"):
        lec_inputs(tmp_path)
