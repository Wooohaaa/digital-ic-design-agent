"""Run reviewed synchronous specification proofs independently of netlist LEC."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from fpga_agent.spec_proof import Cover, Mutation, SpecContract, run_spec, validate_report


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / 'examples/formal'
CONTRACTS = {
    'fifo': SpecContract(
        name='fifo', dut=ROOT/'fpga_agent/templates/conformance/rtl_fifo/fifo_two.sv',
        reference=FORMAL/'fifo_two_spec.sv', dut_top='fifo_two', reference_top='fifo_two_spec',
        inputs={'clk': 1, 'reset_n': 1, 'in_valid': 1, 'out_ready': 1, 'in_word': 16},
        reset='reset_n', active=0,
        projections=(('used', 'used'), ('read_index', 'read_index'), ('write_index', 'write_index'),
                     ('storage[0]', 'slot0'), ('storage[1]', 'slot1')),
        covers={f'reachable_{length}': Cover('length', length, length+2, (('in_valid', 1), ('out_ready', 0)))
                for length in range(3)},
        mutations={
            'reset_loses_to_handshake': Mutation(b'if (!reset_n) begin', b'if (!reset_n && !push) begin',
                                                 'reset_base', 5),
            'full_write_bypass': Mutation(b'push=in_valid && in_ready', b'push=in_valid && (in_ready || out_ready)',
                                          'preservation', 5),
            'wrong_head_slot': Mutation(b'storage[read_index]', b'storage[~read_index]', 'observation', 5),
        },
    ),
    'counter': SpecContract(
        name='counter', dut=ROOT/'examples/lec/counter/rtl/counter.sv',
        reference=FORMAL/'counter_spec.sv', dut_top='counter', reference_top='counter_spec',
        inputs={'clk': 1, 'rst': 1, 'en': 1}, reset='rst', active=1, projections=(),
        covers={
            'reachable_zero': Cover('count', 0, 2),
            'reachable_carry': Cover('count', 16, 18, (('en', 1),)),
            'reachable_maximum': Cover('count', 255, 257, (('en', 1),)),
        },
        mutations={
            'enable_overrides_reset': Mutation(b'if (rst) q', b'if (rst && !en) q', 'reset_base', 3),
            'increments_when_disabled': Mutation(b'else if (en) q', b'else q', 'preservation', 3),
            'saturates_instead_of_wrapping': Mutation(b'else if (en) q', b"else if (en && q != 8'hff) q",
                                                      'preservation', 258),
        },
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('design', choices=tuple(CONTRACTS))
    parser.add_argument('--output', type=Path, required=True, help='New proof and evidence directory')
    parser.add_argument('--yosys', help='Yosys executable; defaults to Yosys or YoWASP on PATH')
    parser.add_argument('--timeout', type=int, default=60, help='Seconds per invocation')
    parser.add_argument('--verify', action='store_true', help='Recheck local evidence without rerunning SAT')
    args = parser.parse_args()
    try:
        if not args.verify:
            run_spec(CONTRACTS[args.design], args.output, yosys=args.yosys, timeout=args.timeout)
        validate_report(CONTRACTS[args.design], args.output.resolve())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'Specification proof failed: {exc}\n')
    print(f'{args.design} specification evidence verified: {args.output / "result.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
