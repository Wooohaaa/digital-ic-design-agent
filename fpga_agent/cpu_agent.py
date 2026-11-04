"""Model generation/repair separated from the locked RV32I verification harness."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
from .model import request_json,ModelOutputError
from .cpu_bench import prepare_project, run_benchmark
from .project import _ansi_top_ports

CPU_CONTRACT = '''Implement a synthesizable RV32I core named rv32_core, ANSI ports:
input wire clk,rst_n;
output wire [31:0] imem_addr; input wire [31:0] imem_rdata;
output wire dmem_valid; output wire [3:0] dmem_wstrb;
output wire [31:0] dmem_addr,dmem_wdata; input wire [31:0] dmem_rdata;
output reg retire_valid; output reg [31:0] retire_pc,retire_insn;
output reg [4:0] retire_rd; output reg [31:0] retire_wdata;
output reg halted,trap.
Memory reads combinational aligned 32-bit word at address&~3, little endian.
Stores at rising edge with dmem_valid and nonzero byte-enable; effective byte address,
byte enables and shifted write data. No repeated/wrong-path stores.
Async reset low: PC0, registers0; x0 hardwired0. Full RV32I arithmetic, shift,
signed/unsigned compare, branches, JAL/JALR, LUI/AUIPC, LB/LBU/LH/LHU/LW,
SB/SH/SW, FENCE nop. No M/C/Zicsr. Misaligned data or taken control transfer,
ECALL/unsupported instruction encoding => halt+trap with no commit or side effect.
For FENCE funct3=0, ignore fm/pred/succ/rs1/rd fields as required for base implementations,
including reserved mode configurations (do not trap these FENCE encodings).
Test EEI: EBREAK 00100073 retires then halt without trap. All other legal
instructions retire once; registered trace at negedge contains original pc/insn,
actual written rd/data or zero/zero for no-write and x0 writes.
No simulator system tasks, no file IO, no testbench, no shell/Tcl/constraints.
Verification is independent and immutable; repair RTL only, never weaken checks.
'''

CPU_SCHEMA={'type':'object','properties':{'rtl':{'type':'string'},'design_spec':{'type':'string'}},'required':['rtl','design_spec'],'additionalProperties':False}
PORTS={**{name:'input' for name in ('clk','rst_n','imem_rdata','dmem_rdata')},**{name:'output' for name in ('imem_addr','dmem_valid','dmem_wstrb','dmem_addr','dmem_wdata','retire_valid','retire_pc','retire_insn','retire_rd','retire_wdata','halted','trap')}}


def validate_core(rtl: str) -> None:
    if not isinstance(rtl,str) or len(rtl.encode())>512000:raise ValueError('RTL is missing or too large')
    if re.search(r'\$(display|write|monitor|strobe|f\w+|readmem\w*|system|stop|finish|fatal)\b',rtl):raise ValueError('CPU RTL may not print, terminate simulation, or access files')
    if re.search(r'`\s*include\b',rtl):raise ValueError('CPU candidate must be self-contained')
    ports,_=_ansi_top_ports(rtl,'rv32_core')
    if ports!=PORTS:raise ValueError(f'core interface differs from trusted contract: {ports}')


def design_cpu(project: Path, architecture: str, *, provider='exchange',model='gpt-6-luna',rounds=2,seeds=64,response_file:Path|None=None) -> dict:
    if architecture not in {'single','multi','pipeline'}:raise ValueError('architecture must be single, multi, or pipeline')
    project=project.resolve();project.mkdir(parents=True,exist_ok=True)
    diagnostics='';previous='';report={'status':'fail','output':'No valid candidate','cases':[]}
    old_response=os.environ.get('FPGA_AGENT_EXCHANGE_RESPONSE_FILE')
    if response_file:os.environ['FPGA_AGENT_EXCHANGE_RESPONSE_FILE']=str(response_file.resolve())
    try:
        for attempt in range(rounds+1):
            audit=project/'generation'/f'design_round_{attempt:02}'
            audit.mkdir(parents=True,exist_ok=True)
            prompt=f'{CPU_CONTRACT}\nArchitecture: {architecture}. Use distinct actual microarchitecture. Return JSON rtl and design_spec.\nCurrent RTL:\n{previous}\nIndependent failure diagnostics:\n{diagnostics}'
            try:
                payload=request_json(prompt,provider=provider,model=model,workspace=project,schema=CPU_SCHEMA,audit_dir=audit)
                rtl=payload.get('rtl');validate_core(rtl)
                spec=payload.get('design_spec')
                if not isinstance(spec,str) or len(spec)>60000:raise ValueError('design_spec missing or too large')
            except (ValueError,ModelOutputError) as exc:
                diagnostics=f'Candidate validation failed before commit: {exc}'
                report={'status':'fail','output':diagnostics,'cases':[]}
                (audit/'validation-error.txt').write_text(diagnostics,encoding='utf-8')
                if provider=='exchange':
                    (project/'generation/repair-request.txt').write_text(prompt+'\n'+diagnostics,encoding='utf-8')
                    break
                continue
            (project/'rtl').mkdir(exist_ok=True);(project/'spec').mkdir(exist_ok=True)
            destination=project/'rtl/rv32_core.v';temp=destination.with_suffix('.v.candidate')
            temp.write_text(rtl.rstrip()+'\n',encoding='utf-8');os.replace(temp,destination)
            (project/'spec/design.md').write_text(spec,encoding='utf-8')
            (project/'spec/brief.md').write_text(CPU_CONTRACT+'\nArchitecture: '+architecture+'\n',encoding='utf-8')
            prepare_project(project)
            manifest=json.loads((project/'project.json').read_text(encoding='utf-8'))
            manifest['verification']['seeds']=seeds;manifest['model_backend']={'model':model,'transport':provider,'architecture':architecture}
            manifest['generated_by']=f'{model} via {provider}'
            (project/'project.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
            report=run_benchmark(project,seeds)
            (audit/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            if report['status']=='pass':return report
            previous=rtl;diagnostics=report['output']+'\n'+json.dumps([c for c in report['cases'] if c['status']=='fail'])
            if provider=='exchange':
                (project/'generation/repair-request.txt').write_text(prompt+'\nNew failure:\n'+diagnostics,encoding='utf-8')
                break  # A fresh external model response is required, never replay a failed one.
        return report
    finally:
        if old_response is None:os.environ.pop('FPGA_AGENT_EXCHANGE_RESPONSE_FILE',None)
        else:os.environ['FPGA_AGENT_EXCHANGE_RESPONSE_FILE']=old_response
