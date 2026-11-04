"""Trusted RV32I differential test and HX1P35A SoC build preparation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone

from .project import load_manifest, save_manifest, template_dir
from .riscv import Program, corpus, run_reference, EBREAK


TB_CPU = r'''`timescale 1ns/1ps
module tb_cpu;
reg clk=0, rst_n=0;
always #10 clk=~clk;
wire [31:0] ia, ir, da, dw, dr, rpc, ri, rw;
wire dv, rv, halted, trap;
wire [3:0] ds;
wire [4:0] rd;
reg [31:0] mem[0:4095];
reg [8191:0] image;
integer k, lane, cycles=0, limit=24000;
assign ir = ia < 16384 ? mem[ia[13:2]] : 32'hffffffff;
assign dr = da < 16384 ? mem[da[13:2]] : 32'h0;
rv32_core dut(.clk(clk),.rst_n(rst_n),.imem_addr(ia),.imem_rdata(ir),
 .dmem_valid(dv),.dmem_wstrb(ds),.dmem_addr(da),.dmem_wdata(dw),.dmem_rdata(dr),
 .retire_valid(rv),.retire_pc(rpc),.retire_insn(ri),.retire_rd(rd),.retire_wdata(rw),.halted(halted),.trap(trap));
initial begin
 if (!$value$plusargs("IMAGE=%s",image)) $fatal(1,"missing IMAGE");
 if ($value$plusargs("LIMIT=%d",limit)) begin end
 $readmemh(image,mem);
 repeat(3) @(negedge clk);
 rst_n=1;
end
always @(posedge clk) begin
 if (!rst_n && dv===1'b1 && ds!==4'b0000) $fatal(1,"store during reset");
 if (rst_n && dv===1'b1 && ds!==4'b0000) begin
  if ((^{da,ds,dw}) === 1'bx || da>=16384) $fatal(1,"invalid store bus");
  $display("STORE %08x %01x %08x",da,ds,dw);
  for(lane=0;lane<4;lane=lane+1) if(ds[lane]) mem[da[13:2]][8*lane+:8]<=dw[8*lane+:8];
 end
end
always @(negedge clk) if(rst_n) begin
 cycles=cycles+1;
 if(rv!==1'b0 && rv!==1'b1) $fatal(1,"unknown retire valid");
 if(rv) begin
  if((^{rpc,ri,rd,rw})===1'bx) $fatal(1,"unknown retirement");
  $display("RETIRE %08x %08x %02x %08x",rpc,ri,rd,rw);
 end
 if(halted===1'b1) begin
  $display("STOP %01x %0d",trap,cycles);
  for(k=1024;k<1088;k=k+1) $display("MEM %08x %08x",k*4,mem[k]);
  $finish;
 end
 if(cycles>limit) $fatal(1,"watchdog timeout");
end
endmodule
'''


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verification_hashes(project: Path, manifest: dict) -> dict[str, str]:
    return {name: digest(project / name) for name in manifest['rtl_sources'] + manifest['testbench_sources']}


def trusted_hashes(project: Path) -> dict[str, str]:
    files = [Path(__file__), Path(__file__).with_name('riscv.py')]
    return {file.name: digest(file) for file in files}


def tool_environment() -> tuple[str, str, dict[str, str]]:
    env = os.environ.copy()
    local = Path(__file__).resolve().parent.parent / '.tools/icarus/bin'
    if local.is_dir():
        env['PATH'] = str(local) + os.pathsep + env.get('PATH', '')
    compiler, runtime = shutil.which('iverilog', path=env['PATH']), shutil.which('vvp', path=env['PATH'])
    if not compiler or not runtime:
        raise RuntimeError('Icarus Verilog iverilog/vvp is required')
    return compiler, runtime, env


def board_program() -> Program:
    directed = corpus(0)
    selected = [p for p in directed if p.name in {'alu_edges','memory_lanes','backward_loop'} or (p.name.startswith('branch_') and p.name.split('_')[1] in {'0','1','4','5','6','7'})]
    return Program('board_selftest', [word for p in selected for word in p.words[:-1]] + [EBREAK])


def signature(trace: list[tuple[int,int,int,int]]) -> int:
    value = 0
    for pc, insn, rd, data in trace:
        value = (((value << 1) | (value >> 31)) ^ pc ^ insn ^ rd ^ data) & 0xFFFFFFFF
    return value


def make_soc(program: Program) -> str:
    cases = '\n'.join(f"   10'd{i}: rom=32'h{word:08x};" for i,word in enumerate(program.words))
    return '''`timescale 1ns/1ps
module rv32_soc(input wire I_sys_clk,input wire I_rst_n,output reg O_screen_pwm);
wire [31:0] ia,ir,da,dw,dr,rpc,ri,rw;
wire dv,rv,halted,trap;
wire [3:0] ds;
wire [4:0] rd;
reg [31:0] ram[0:63];
reg [31:0] signature;
reg [15:0] divider;
reg [4:0] bit_index;
integer k,lane;
function [31:0] rom;
 input [9:0] index;
 begin
  case(index)
''' + cases + '''
   default:rom=32'h00100073;
  endcase
 end
endfunction
assign ir=rom(ia[11:2]);
assign dr=(da>=32'h1000 && da<32'h1100)?ram[da[7:2]]:32'h0;
rv32_core cpu(.clk(I_sys_clk),.rst_n(I_rst_n),.imem_addr(ia),.imem_rdata(ir),
 .dmem_valid(dv),.dmem_wstrb(ds),.dmem_addr(da),.dmem_wdata(dw),.dmem_rdata(dr),
 .retire_valid(rv),.retire_pc(rpc),.retire_insn(ri),.retire_rd(rd),.retire_wdata(rw),.halted(halted),.trap(trap));
always @(posedge I_sys_clk or negedge I_rst_n) begin
 if(!I_rst_n) begin
  for(k=0;k<64;k=k+1) ram[k]<=0;
 end else if(dv && da>=32'h1000 && da<32'h1100)
  for(lane=0;lane<4;lane=lane+1) if(ds[lane]) ram[da[7:2]][8*lane+:8]<=dw[8*lane+:8];
end
always @(posedge I_sys_clk or negedge I_rst_n) begin
 if(!I_rst_n) begin signature<=0; divider<=0; bit_index<=0; O_screen_pwm<=0;end
 else begin
  if(rv) signature<={signature[30:0],signature[31]} ^ rpc ^ ri ^ rw ^ {27'b0,rd};
  divider<=divider+1'b1;
  if(divider==0) begin
   bit_index<=bit_index+1'b1;
   O_screen_pwm<=trap?1'b1:(halted?signature[bit_index]:bit_index[4]);
  end
 end
end
endmodule
'''


def make_soc_testbench(expected_signature: int) -> str:
    return '''`timescale 1ns/1ps
module tb_soc;
reg clk=0,rst_n=0; wire out;
always #10 clk=~clk;
rv32_soc dut(.I_sys_clk(clk),.I_rst_n(rst_n),.O_screen_pwm(out));
initial begin repeat(3) @(negedge clk); rst_n=1;
 wait(dut.halted); repeat(2) @(negedge clk);
 if(dut.trap!==0) $fatal(1,"SoC unexpected trap");
 if(dut.signature!==32'hSIGNATURE) $fatal(1,"SoC signature mismatch %08x",dut.signature);
 $display("PASS: board ROM/RAM signature");$finish;
end
initial begin #1000000;$fatal(1,"SoC timeout");end
endmodule
'''.replace('SIGNATURE',f'{expected_signature:08x}')


def prepare_project(project: Path) -> None:
    project=project.resolve()
    if not (project/'rtl/rv32_core.v').is_file():
        raise FileNotFoundError(project/'rtl/rv32_core.v')
    for folder in ('board','constraints','tb','reports','spec','generation'):
        (project/folder).mkdir(parents=True,exist_ok=True)
    template=template_dir()
    shutil.copy2(template/'board/hx1p35a.json',project/'board/hx1p35a.json')
    shutil.copy2(template/'constraints/hx1p35a.adc',project/'constraints/hx1p35a.adc')
    shutil.copy2(template/'constraints/hx1p35a.sdc',project/'constraints/hx1p35a.sdc')
    program=board_program()
    expected=run_reference(program)
    (project/'rtl/rv32_soc.v').write_text(make_soc(program),encoding='utf-8')
    (project/'tb/tb_cpu.v').write_text(TB_CPU,encoding='utf-8')
    soc_tb=make_soc_testbench(signature(expected.trace))
    (project/'tb/tb_soc.v').write_text(soc_tb,encoding='utf-8')
    manifest={
        'project':project.name,'agent_version':'0.2.0','board':'HX1P35A','device_family':'ph1','device':'PH1P35MDG324',
        'top_module':'rv32_soc','testbench_top':'tb_cpu','rtl_sources':['rtl/rv32_core.v','rtl/rv32_soc.v'],
        'testbench_sources':['tb/tb_cpu.v','tb/tb_soc.v'],'board_constraints':'constraints/hx1p35a.adc',
        'timing_constraints':'constraints/hx1p35a.sdc','td_full_allowed':True,
        'verification':{'kind':'riscv-retire','seeds':64,'trusted_files':['tb/tb_cpu.v','tb/tb_soc.v'],'eei':'bare-metal-test-no-CSR'},
        'board_signature':f'{signature(expected.trace):08x}',
        'trusted_testbench_hashes':{name:digest(project/name) for name in ('tb/tb_cpu.v','tb/tb_soc.v')},
        'generated_by':'candidate source; model provenance supplied by generation backend',
    }
    save_manifest(project,manifest)
    provenance=project/'generation/provenance.json'
    if provenance.exists():
        info=json.loads(provenance.read_text(encoding='utf-8'))
        info['initial_core_sha256']=info.get('initial_core_sha256',digest(project/'rtl/rv32_core.v'))
        provenance.write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    original=project/'generation/initial_core.v'
    if not original.exists():shutil.copy2(project/'rtl/rv32_core.v',original)


def parse_and_compare(output: str, expected) -> dict:
    trace,stores,memory,stops=[],[],{},[]
    for line in output.splitlines():
        parts=line.split()
        if not parts:continue
        if parts[0]=='RETIRE':trace.append(tuple(int(v,16) for v in parts[1:]))
        elif parts[0]=='STORE':
            address,mask,value=(int(v,16) for v in parts[1:])
            byte_mask=sum(255<<(8*lane) for lane in range(4) if mask&(1<<lane))
            stores.append((address,mask,value&byte_mask))
        elif parts[0]=='MEM':memory[int(parts[1],16)]=int(parts[2],16)
        elif parts[0]=='STOP':stops.append((int(parts[1],16),int(parts[2])))
    for label,actual,wanted in [('retirement',trace,expected.trace),('stores',stores,expected.stores)]:
        if actual!=wanted:
            limit=min(len(actual),len(wanted))
            mismatch=next((i for i in range(limit) if actual[i]!=wanted[i]),limit)
            raise AssertionError(f'{label} mismatch at {mismatch}: actual={actual[mismatch:mismatch+1]} expected={wanted[mismatch:mismatch+1]} lengths={len(actual)}/{len(wanted)}')
    if memory!=expected.memory:raise AssertionError('final data memory mismatch')
    if len(stops)!=1 or stops[0][0]!=int(expected.trap):raise AssertionError(f'halting/trap mismatch: {stops}, expected trap={expected.trap}')
    return {'retired':len(trace),'stores':len(stores),'cycles':stops[0][1],'trap':expected.trap}


def run_benchmark(project: Path,seeds: int|None=None) -> dict:
    project=project.resolve();manifest=load_manifest(project)
    if (project/'tb/tb_cpu.v').read_text(encoding='utf-8')!=TB_CPU:
        raise ValueError('Trusted CPU testbench was modified. Regenerate it before verification.')
    program=board_program()
    if (project/'rtl/rv32_soc.v').read_text(encoding='utf-8')!=make_soc(program):
        raise ValueError('Trusted board SoC wrapper was modified.')
    if (project/'tb/tb_soc.v').read_text(encoding='utf-8')!=make_soc_testbench(signature(run_reference(program).trace)):
        raise ValueError('Trusted board SoC testbench was modified.')
    if set(manifest.get('trusted_testbench_hashes',{}))!={'tb/tb_cpu.v','tb/tb_soc.v'}:
        raise ValueError('Complete trusted testbench fingerprints are required.')
    for name,expected_hash in manifest.get('trusted_testbench_hashes',{}).items():
        if digest(project/name)!=expected_hash:raise ValueError(f'Trusted testbench changed: {name}')
    start_sources=verification_hashes(project,manifest);start_harness=trusted_hashes(project)
    compiler,runtime,env=tool_environment()
    seeds=manifest.get('verification',{}).get('seeds',64) if seeds is None else seeds
    if not isinstance(seeds,int) or not 0<=seeds<=1024:raise ValueError('seeds must be 0..1024')
    build=project/'build/cpu';build.mkdir(parents=True,exist_ok=True)
    reports=project/'reports';reports.mkdir(exist_ok=True)
    binary=build/'cpu.vvp'
    cmd=[compiler,'-g2012','-Wall','-s','tb_cpu','-o',str(binary)]+[str(project/p) for p in manifest['rtl_sources']]+[str(project/'tb/tb_cpu.v')]
    start=time.monotonic();cases=[];coverage=set();log=[]
    compiled=subprocess.run(cmd,cwd=project,env=env,capture_output=True,text=True,timeout=120)
    log.append(compiled.stdout+compiled.stderr)
    if compiled.returncode==0:
        for program in corpus(seeds):
            directory=build/program.name;directory.mkdir(exist_ok=True)
            words=program.words+[0]*(4096-len(program.words))
            (directory/'program.hex').write_text('\n'.join(f'{w:08x}' for w in words)+'\n',encoding='ascii')
            expected=run_reference(program);coverage.update(expected.coverage)
            (directory/'expected.json').write_text(json.dumps({'trace':expected.trace,'stores':expected.stores,'trap':expected.trap,'memory':expected.memory},indent=2),encoding='utf-8')
            try:
                run=subprocess.run([runtime,str(binary),f'+IMAGE={(directory/"program.hex").as_posix()}',f'+LIMIT={program.max_steps*8+20}'],cwd=project,env=env,capture_output=True,text=True,timeout=15)
                output=run.stdout+run.stderr
                (directory/'actual.log').write_text(output,encoding='utf-8')
                if run.returncode:raise AssertionError(f'simulator exit {run.returncode}: {output[-800:]}')
                metrics=parse_and_compare(output,expected)
                cases.append({'name':program.name,'status':'pass',**metrics})
            except Exception as exc:
                cases.append({'name':program.name,'status':'fail','error':str(exc)})
                log.append(f'FAIL {program.name}: {exc}')
                # Keep the first counterexample concise for a model repair.
                break
    # Exercise the exact ROM, RAM and retirement-signature SoC used by TD.
    soc_status='not_run'
    if compiled.returncode==0 and cases and all(c['status']=='pass' for c in cases):
        socbin=build/'soc.vvp'
        soc_compile=subprocess.run([compiler,'-g2012','-s','tb_soc','-o',str(socbin)]+[str(project/p) for p in manifest['rtl_sources']]+[str(project/'tb/tb_soc.v')],env=env,cwd=project,capture_output=True,text=True,timeout=120)
        soc_run=subprocess.run([runtime,str(socbin)],env=env,cwd=project,capture_output=True,text=True,timeout=20) if soc_compile.returncode==0 else soc_compile
        soc_output=soc_compile.stdout+soc_compile.stderr+soc_run.stdout+soc_run.stderr
        (build/'soc.log').write_text(soc_output,encoding='utf-8')
        soc_status='pass' if soc_run.returncode==0 and 'PASS: board ROM/RAM signature' in soc_output else 'fail'
        log.append(soc_output)
    passed=compiled.returncode==0 and len(cases)==len(corpus(seeds)) and all(c['status']=='pass' for c in cases) and soc_status=='pass'
    if verification_hashes(project,load_manifest(project))!=start_sources or trusted_hashes(project)!=start_harness:
        passed=False;log.append('Verification inputs changed while simulation was running.')
    report={'status':'pass' if passed else 'fail','verification_kind':'riscv-retire','simulator':'Icarus Verilog',
        'started_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.monotonic()-start,3),
        'source_hashes':start_sources,'trusted_harness_hashes':start_harness,
        'seeds':seeds,'case_count':len(cases),'cases':cases,'instruction_coverage':sorted(coverage),'board_soc':soc_status,
        'output':'\n'.join(log),'command':cmd}
    (reports/'simulation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (reports/'simulation.log').write_text(report['output'],encoding='utf-8')
    print(f"RV32I {project.name}: {report['status']}, {len(cases)} cases, SoC={soc_status}, {report['elapsed_seconds']}s",flush=True)
    if not passed:print(report['output'][-3000:],flush=True)
    return report
