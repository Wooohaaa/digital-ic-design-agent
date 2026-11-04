"""Reproducible TinyStories260K software/RTL/FPGA trial; hardware is a separate gate."""
from __future__ import annotations
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

from .evidence import EVIDENCE_VERSION, sha, simulation_input_hashes, source_hashes
from .project import load_manifest, save_manifest
from .terminal import write_json

TEMPLATE = Path(__file__).parent / 'templates/tinystories260k'
ASSETS = {
    'stories260K.bin': 'b0a507e7ad0f626624f17112325e66691f9076d622e1d3274d103d00299f2696',
    'tok512.bin': '037cb335abb25d1fa9e8ecae30ed2a3a8ace9302862ebcdc05d51a6bbb10c312',
}
PINS = {'I_sys_clk':'C4','I_uart_rx':'J1','O_uart_tx':'H1', 'I_flash_miso':'D16',
        'O_flash_mosi':'E13','O_flash_clk':'N6','O_flash_cs_n':'D14',
        'O_flash_wp_n':'C16','O_flash_hold_n':'A13'}
BOARD_DOCUMENTS = {
    '新HX1P35A开发板手册-HDL版2604.pdf':'7b7a65b0212db35cb89a5dff6e80ae6cedf4ef697c0e61e20829c0dc801468d4',
    'HX1P35B开发板引脚分配汇总.docx':'487f4adeaa726ac9d676e4348c88a1446a4befdb40d29184fed3cc5c479d9cfb',
    '开发板原理图/HX1P35B_FT_40P_8.pdf':'403e6dc2bf4952c060f3f5b5e0ccc9533e840e2c096c7c9563bc1a4a654c935c',
}

def environment() -> dict:
    env = os.environ.copy()
    # Icarus ships old MinGW DLLs. Put the compiler runtime first; Icarus finds
    # its private DLLs next to its executable without poisoning GCC/Verilator.
    extra = [r'C:\msys64\ucrt64\bin',r'C:\msys64\usr\bin',
             str(Path(__file__).resolve().parents[1]/'.tools/icarus/bin')]
    env['PATH'] = os.pathsep.join(extra+[env.get('PATH','')])
    if Path(r'C:\msys64\ucrt64\share\verilator').is_dir():
        env.setdefault('VERILATOR_ROOT','C:/msys64/ucrt64/share/verilator')
    return env

def run(command: list[str], project: Path, *, timeout=180, name='command') -> subprocess.CompletedProcess:
    print(f'[{name}]', flush=True)
    log = project/'build'/f'{name}.txt'
    log.parent.mkdir(parents=True,exist_ok=True)
    # Write long-running output directly to disk so it survives timeout/cancellation.
    with log.open('w',encoding='utf-8') as stream:
        result = subprocess.run(command,cwd=project,env=environment(),stdout=stream,
                                stderr=subprocess.STDOUT,text=True,timeout=timeout,check=False)
    result.stdout = log.read_text(encoding='utf-8',errors='replace')
    if result.returncode:
        raise RuntimeError(f'{name} failed ({result.returncode}):\n{result.stdout[-6000:]}')
    return result

def gcc_path() -> Path:
    override=os.environ.get('ICFORGE_RISCV_GCC')
    candidates=[Path(override)] if override else []
    candidates += [Path.home()/'.platformio/packages/toolchain-riscv32-esp/bin/riscv32-esp-elf-gcc.exe']
    found=shutil.which('riscv32-unknown-elf-gcc') or shutil.which('riscv32-esp-elf-gcc')
    if found: candidates.append(Path(found))
    for p in candidates:
        if p.is_file(): return p
    raise RuntimeError('RISC-V GCC missing; set ICFORGE_RISCV_GCC to an rv32i/ilp32 toolchain.')

def assets(project: Path, source: Path | None = None) -> None:
    directory=project/'model'; directory.mkdir(exist_ok=True)
    for name,digest in ASSETS.items():
        target=directory/name
        if not target.exists():
            if source and (source/name).is_file(): shutil.copy2(source/name,target)
            else:
                url=f'https://huggingface.co/karpathy/tinyllamas/resolve/main/stories260K/{name}'
                temporary=target.with_suffix('.download')
                with urllib.request.urlopen(url,timeout=120) as response:
                    temporary.write_bytes(response.read(2_000_001))
                if sha(temporary)!=digest:
                    temporary.unlink(missing_ok=True)
                    raise ValueError('Downloaded checkpoint/tokenizer SHA-256 mismatch: '+name)
                temporary.replace(target)
        if sha(target)!=digest: raise ValueError('Checkpoint/tokenizer SHA-256 mismatch: '+name)
    config=struct.unpack('<7i',(directory/'stories260K.bin').read_bytes()[:28])
    if config!=(64,172,5,8,4,512,512): raise ValueError('Unsupported model configuration')
    write_json(directory/'manifest.json',{'source':'https://huggingface.co/karpathy/tinyllamas/tree/main/stories260K',
        'license':'MIT','sha256':ASSETS,'config':list(config),'bytes':1056540,
        'deployment_context':64,'checkpoint_context':512,'quantization':'none; FP32'})

def prepare(project: Path, *, board_docs: Path | None = None, source: Path | None = None) -> None:
    project.mkdir(parents=True,exist_ok=True)
    for folder in ('rtl','tb','firmware','board','constraints','spec','generation','reports','build'):
        (project/folder).mkdir(exist_ok=True)
    # Existing candidate RTL is preserved. Fresh projects receive the audited Luna sources.
    for src in TEMPLATE.rglob('*'):
        if src.is_file():
            dst=project/src.relative_to(TEMPLATE)
            if not dst.exists(): dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
    assets(project,source)
    if board_docs is not None:
        for name,digest in BOARD_DOCUMENTS.items():
            if not (board_docs/name).is_file() or sha(board_docs/name)!=digest:
                raise ValueError('Board documentation differs from reviewed HDL2604 profile: '+name)
    board={'board':'HX1P35A HDL2604 documentation set','device':'PH1P35MDG324','device_family':'ph1',
        'nominal_system_clock_hz':50000000,'source_documents':BOARD_DOCUMENTS,
        'reviewed_pages':{'manual':[6,12,21,22],'schematic':'sheet 9 / file 8'},
        'revision_note':'Manual title A, schematic/pin table B; physical board label still requires inspection.',
        'signals':{n:{'pin':p,'direction':'input' if n.startswith('I_') else 'output','io_standard':'LVCMOS33'} for n,p in PINS.items()},
        'known_legacy_conflicts':{'R10':'Ethernet TXEN; not reset','R11':'Ethernet TXD0; not screen PWM'},
        'flash':{'user':'N25Q128; QSPI_CS/D0/D1/D2/D3/CLK', 'configuration':'GD25WQ128; R8/U12/V12/V11/U11/R15; never write', 'jedec_id':'20ba18'},
        'reset':'internal 255-cycle FPGA power-on counter; no external reset pin'}
    write_json(project/'board/hx1p35a.json',board)
    (project/'constraints/hx1p35a.adc').write_text('\n'.join(
        f'set_pin_assignment {{ {n} }} {{ LOCATION = {p}; IOSTANDARD = LVCMOS33; }}' for n,p in PINS.items())+'\n',encoding='utf-8')
    (project/'constraints/hx1p35a.sdc').write_text(
        'create_clock -name sys_clk_50m -period 20.000 [get_ports {I_sys_clk}]\n'
        'set_false_path -from [get_ports {I_uart_rx}]\n'
        'set_input_delay -clock sys_clk_50m -max 8.0 [get_ports {I_flash_miso}]\n'
        'set_input_delay -clock sys_clk_50m -min 0.0 [get_ports {I_flash_miso}]\n'
        'set_output_delay -clock sys_clk_50m -max 2.0 [get_ports {O_flash_* O_uart_tx}]\n'
        'set_output_delay -clock sys_clk_50m -min 0.0 [get_ports {O_flash_* O_uart_tx}]\n',encoding='utf-8')
    brief='Deploy the ESP32 TinyStories260K checkpoint on HX1P35A FPGA; use actual GPT-6 Luna generation, independent verification, TD implementation and explicit hardware evidence.'
    design='''# TinyStories260K on HX1P35A\n
FP32 checkpoint: dim=64, hidden=172, 5 layers, 8 query heads, 4 KV heads, vocabulary=512.
All embedding, RMSNorm, GQA causal attention, RoPE, SwiGLU and classifier computation executes
in a Luna RV32I softcore with libgcc software float. Model weights are read-only mapped from
the independent 16MiB N25Q128 user QSPI NOR at 0x10000000. The FPGA configuration Flash is not addressed.
ROM=32KiB at 0; RAM=128KiB at 0x20000000. KV uses 80KiB for 64 tokens, not the checkpoint's
512-token context. No quantization, retraining, Xtensa ISA emulation, or ASIC fabrication.
50MHz single clock; SPI mode0 12.5MHz; UART115200 8N1 with two-flop RX synchronizer.
Power-on reset uses an initialized 8-bit counter and releases synchronously. UART is asynchronous;
SPI MISO follows the generated SPI timing. The SDC IO budgets require hardware review.
UART monitor G takes prompt token IDs and total forward steps. T also emits all 512 logits.
Tokenization and text decoding are host I/O; inference runs on FPGA. W stop-and-wait programs
only the independent user Flash, checks JEDEC/capacity, bounds, busy watchdog and CRC readback.
Actual hardware success requires a current bitstream, full image CRC match and board UART
logits/token evidence; offline simulation does not grant hardware deployment status.
Reference: karpathy/llama2.c and karpathy/tinyllamas (MIT); DaveBben/esp32-llm for workload.
The manual/pin table naming A/B differs: inspect physical revision before programming.
'''
    (project/'spec/brief.md').write_text(brief+'\n',encoding='utf-8')
    (project/'spec/design.md').write_text(design,encoding='utf-8')
    requirements=['SPI byte order, address and no duplicate held request','SPI reset recovery',
        'UART 8N1 receive/transmit','RV32I retirement/store comparison under wait states',
        '64-position independent FP32 logits comparison','Production firmware full RTL inference/logits',
        'Context/checkpoint/token bounds rejection']
    write_json(project/'spec/verification-plan.json',{'requirements':[
        {'id':f'REQ-{i+1:03}','requirement':r} for i,r in enumerate(requirements)]})
    inputs=[p.relative_to(project).as_posix() for d in ('firmware','model') for p in (project/d).rglob('*') if p.is_file() and p.suffix not in ('.elf','.map','.bin')]
    inputs += ['model/stories260K.bin','model/tok512.bin','tb/sim_main.cpp','tb/tiny_sim.v','board/hx1p35a.json']
    manifest={'project':project.name,'top_module':'tinystories_soc','testbench_top':'tb_io',
        'device_family':'ph1','device':'PH1P35MDG324','board':'HX1P35A HDL2604',
        'rtl_sources':['rtl/luna_rv32.v','rtl/luna_io.v','rtl/tinystories_soc.v'],
        'testbench_sources':['tb/tb_io.v'],'board_constraints':'constraints/hx1p35a.adc',
        'timing_constraints':'constraints/hx1p35a.sdc','board_io':{n:n for n in PINS},
        'top_ports':{n:'input' if n.startswith('I_') else 'output' for n in PINS},
        'unmapped_top_ports':[],'td_full_allowed':True,'checks':requirements,
        'verification':{'kind':'tinystories-fp32','context':64,'rtl_steps':3,'additional_inputs':sorted(set(inputs))},
        'model_backend':{'transport':'current-session Luna generation + exchange bridge','model':'gpt-6-luna',
                         'components':['CPU RTL','SPI NOR reader RTL','UART RTL'], 'integration':'IC Forge host engineering harness'}}
    save_manifest(project,manifest)
    if not (project/'reports/hardware.json').exists():
        write_json(project/'reports/hardware.json',{'status':'not_run','reason':'No physical download/UART evidence collected.'})

def build(project: Path) -> dict:
    assets(project)
    gcc=gcc_path(); bindir=gcc.parent
    checksum=zlib.crc32((project/'model/stories260K.bin').read_bytes())
    (project/'firmware/model_contract.h').write_text(f'#define MODEL_CRC32 0x{checksum:08x}u\n',encoding='ascii')
    rope=[[[math.cos(p*10.0**(-i)),math.sin(p*10.0**(-i))] for i in range(4)] for p in range(64)]
    def literal(x): return f'{x:.9e}f'
    (project/'firmware/rope.h').write_text('static const float rope[64][4][2]={\n'+',\n'.join(
        '{'+','.join('{'+literal(c)+','+literal(s)+'}' for c,s in row)+'}' for row in rope)+'\n};\n',encoding='utf-8')
    command=[str(gcc),'-march=rv32i','-mabi=ilp32','-Os','-ffreestanding','-fno-builtin',
        '-ffp-contract=off','-fno-fast-math','-ffunction-sections','-fdata-sections',
        '-msmall-data-limit=0','-mno-relax','-nostdlib','-Wl,--gc-sections',
        '-Wl,-Map=firmware/firmware.map','-T','firmware/link.ld','firmware/start.S',
        'firmware/inference.c','firmware/monitor.c','-lm','-lgcc','-o','firmware/firmware.elf']
    run(command,project,name='firmware-build')
    objcopy=next(bindir.glob('*objcopy.exe'))
    run([str(objcopy),'-O','binary','firmware/firmware.elf','firmware/firmware.bin'],project,name='objcopy')
    data=(project/'firmware/firmware.bin').read_bytes()
    if len(data)>32768: raise ValueError('Firmware exceeds 32KiB ROM')
    padded=data+bytes(32768-len(data))
    (project/'firmware/rom.hex').write_text(''.join(f'{x:08x}\n' for (x,) in struct.iter_unpack('<I',padded)),encoding='ascii')
    native=shutil.which('gcc',path=environment()['PATH'])
    if not native: raise RuntimeError('Native GCC required for independent host comparison')
    run([native,'-O2','-shared','-ffp-contract=off','-fno-fast-math','firmware/inference.c',
         '-lm','-o','build/native.dll'],project,name='native-build')
    sources={p.relative_to(project).as_posix():sha(p) for p in (project/'firmware').iterdir() if p.suffix in {'.c','.S','.ld','.h'}}
    report={'status':'pass','rom_bytes':len(data),'rom_capacity':32768,'ram_capacity':131072,
        'kv_bytes':81920,'context':64,'source_hashes':sources,'rom_sha256':sha(project/'firmware/rom.hex'),
        'compiler':run([str(gcc),'--version'],project,name='compiler-version').stdout.splitlines()[0]}
    write_json(project/'reports/firmware.json',report)
    return report

class Reference:
    """Independent NumPy implementation; does not call candidate C or RTL."""
    def __init__(self,checkpoint: Path):
        import numpy as np
        self.np=np
        raw=checkpoint.read_bytes(); h=struct.unpack('<7i',raw[:28])
        if h!=(64,172,5,8,4,512,512): raise ValueError('Unexpected reference checkpoint')
        data=np.frombuffer(raw,dtype='<f4',offset=28); cursor=0
        self.weights={}
        shapes={'emb':(512,64),'ra':(5,64),'wq':(5,64,64),'wk':(5,32,64),
                'wv':(5,32,64),'wo':(5,64,64),'rf':(5,64),'w1':(5,172,64),
                'w2':(5,64,172),'w3':(5,172,64),'rfinal':(64,)}
        for name,shape in shapes.items():
            size=math.prod(shape); self.weights[name]=data[cursor:cursor+size].reshape(shape); cursor+=size
        self.k=np.zeros((5,64,32),dtype=np.float32); self.v=self.k.copy()
    def forward(self,token:int,pos:int):
        np=self.np; w=self.weights
        if not 0<=token<512 or not 0<=pos<64: raise ValueError('Reference token/context bounds')
        def norm(a,weight): return weight*(a/np.sqrt(np.mean(a*a,dtype=np.float32)+np.float32(1e-5)))
        x=w['emb'][token].copy()
        for l in range(5):
            z=norm(x,w['ra'][l]); q=w['wq'][l]@z
            k=w['wk'][l]@z; self.v[l,pos]=w['wv'][l]@z
            for a in (q,k):
                pairs=a.reshape(-1,4,2); old=pairs.copy()
                angles=np.float32(pos)*np.array([1,.1,.01,.001],dtype=np.float32)
                c=np.cos(angles); s=np.sin(angles)
                pairs[:,:,0]=old[:,:,0]*c-old[:,:,1]*s
                pairs[:,:,1]=old[:,:,0]*s+old[:,:,1]*c
            self.k[l,pos]=k
            heads=[]
            for h in range(8):
                khead=self.k[l,:pos+1,h//2*8:h//2*8+8]
                scores=(khead@q[h*8:h*8+8])/np.sqrt(np.float32(8))
                prob=np.exp(scores-np.max(scores)); prob/=np.sum(prob,dtype=np.float32)
                heads.append(prob@self.v[l,:pos+1,h//2*8:h//2*8+8])
            x+=w['wo'][l]@np.concatenate(heads)
            z=norm(x,w['rf'][l]); a=w['w1'][l]@z; b=w['w3'][l]@z
            x+=w['w2'][l]@((a/(np.float32(1)+np.exp(-a)))*b)
        return w['emb']@norm(x,w['rfinal'])

def native_verify(project: Path) -> dict:
    import numpy as np
    image=(project/'model/stories260K.bin').read_bytes()
    buffer=ctypes.create_string_buffer(image)
    dll=ctypes.CDLL(str(project/'build/native.dll'))
    dll.ts_init.argtypes=[ctypes.c_void_p]; dll.ts_forward.argtypes=[ctypes.c_int,ctypes.c_int]
    dll.ts_logits.restype=ctypes.POINTER(ctypes.c_float)
    if dll.ts_init(buffer): raise ValueError('C checkpoint initialization failed')
    reference=Reference(project/'model/stories260K.bin'); max_error=0.0; cases=[]
    rng=np.random.default_rng(260)
    for pos,token in enumerate([1]+rng.integers(3,512,size=63).tolist()):
        expected=reference.forward(token,pos); actual_best=dll.ts_forward(token,pos)
        actual=np.ctypeslib.as_array(dll.ts_logits(),shape=(512,)).copy()
        error=float(np.max(np.abs(actual-expected))); max_error=max(max_error,error)
        if not np.all(np.isfinite(actual)) or error>0.002 or actual_best!=int(np.argmax(expected)):
            raise AssertionError(f'C/NumPy logits mismatch at position {pos}: error={error}, argmax={actual_best}/{np.argmax(expected)}')
        cases.append({'position':pos,'token':token,'argmax':actual_best,'max_absolute_error':error})
    if dll.ts_forward(512,0)!=-1 or dll.ts_forward(1,64)!=-1 or dll.ts_forward(-1,0)!=-1:
        raise AssertionError('C context/token bounds not rejected')
    bad=ctypes.create_string_buffer(bytes(len(image)))
    if dll.ts_init(bad)!=-1: raise AssertionError('Invalid checkpoint accepted')
    report={'status':'pass','positions':64,'logits_compared':32768,'max_absolute_error':max_error,
            'absolute_tolerance':0.002,'argmax_matches':64,'bounds':'pass','cases':cases}
    write_json(project/'reports/native-reference.json',report)
    return report

def cpu_verify(project: Path, seeds=64) -> dict:
    from .cpu_bench import TB_CPU, tool_environment, parse_and_compare
    from .riscv import corpus,run_reference
    compiler,runtime,env=tool_environment()
    # Use the existing independent ISA checker, with real variable memory stalls.
    tb=TB_CPU.replace('wire dv, rv, halted, trap;',
        'wire dv, rv, halted, trap; wire raw_dv; reg [7:0] stall=0; always @(posedge clk) stall<=stall+1;\n'
        'wire imem_ready=stall[1:0]==1; wire dmem_ready=stall[2:0]==5; assign dv=raw_dv&&dmem_ready;')
    tb=tb.replace('rv32_core dut(', 'luna_rv32 dut(.imem_valid(),.imem_ready(imem_ready),.dmem_ready(dmem_ready),')
    tb=tb.replace('.dmem_valid(dv)', '.dmem_valid(raw_dv)')
    (project/'build/tb_cpu_stall.v').write_text(tb,encoding='utf-8')
    run([compiler,'-g2012','-s','tb_cpu','-o','build/cpu.vvp','rtl/luna_rv32.v','build/tb_cpu_stall.v'],project,name='cpu-compile')
    cases=[]; retired=0
    for case in corpus(seeds):
        memory=[0]*4096; memory[:len(case.words)]=case.words
        path=project/'build/program.hex'; path.write_text(''.join(f'{v:08x}\n' for v in memory),encoding='ascii')
        result=subprocess.run([runtime,'build/cpu.vvp','+IMAGE=build/program.hex','+LIMIT=240000'],
            cwd=project,env=env,text=True,capture_output=True,timeout=30,check=False)
        if result.returncode: raise AssertionError('CPU simulation failed: '+case.name+'\n'+result.stdout)
        try: compared=parse_and_compare(result.stdout,run_reference(case))
        except AssertionError:
            (project/'build/cpu-failure.txt').write_text(result.stdout,encoding='utf-8'); raise
        retired+=compared['retired']; cases.append({'name':case.name,'status':'pass',**compared})
    report={'status':'pass','case_count':len(cases),'retired':retired,'seeds':seeds,'wait_states':True,'cases':cases}
    write_json(project/'reports/cpu-waitstates.json',report); return report

def rtl_verify(project: Path) -> dict:
    import numpy as np
    verilator=shutil.which('verilator_bin',path=environment()['PATH'])
    if not verilator: raise RuntimeError('Verilator missing (full firmware RTL simulation required)')
    sources=load_manifest(project)['rtl_sources']
    run([verilator,'--cc','--exe','--top-module','tiny_sim',
         '--Mdir','build/verilated','--compiler','gcc',
         '-CFLAGS','-std=c++17','-Wno-fatal',*sources,'tb/tiny_sim.v',(project/'tb/sim_main.cpp').as_posix()],project,timeout=240,name='verilator-generate')
    make=shutil.which('mingw32-make',path=environment()['PATH']) or shutil.which('make',path=environment()['PATH'])
    if not make: raise RuntimeError('GNU Make required for Verilator build')
    run([make,'-B','-C','build/verilated','-f','Vtiny_sim.mk','-j4','CXX=g++','LINK=g++'],project,timeout=240,name='verilator-build')
    # Exercise all layers, GQA history and RoPE with three actual production forwards.
    script=bytes([ord('T'),3,2])+struct.pack('<HH',1,382)+b'X'
    (project/'build/uart-script.bin').write_bytes(script)
    executable=project/'build/verilated/Vtiny_sim.exe'
    result=run([str(executable),'model/stories260K.bin','build/uart-script.bin'],project,timeout=1800,name='rtl-inference')
    (project/'reports/rtl-uart.txt').write_text(result.stdout,encoding='utf-8')
    reference=Reference(project/'model/stories260K.bin'); next_token=1; errors=[]; outputs=[]
    lines=re.findall(r'^LOGITS (\d+) (.+)$',result.stdout,re.M)
    if len(lines)!=3: raise AssertionError(f'Incomplete RTL logits: {len(lines)}/3')
    for pos_text,line in lines:
        pos=int(pos_text); token=[1,382][pos] if pos<2 else next_token
        expected=reference.forward(token,pos)
        words=line.split()
        if len(words)!=512: raise AssertionError('Incomplete UART logits frame')
        actual=np.array([struct.unpack('<f',struct.pack('<I',int(v,16)))[0] for v in words],dtype=np.float32)
        error=float(np.max(np.abs(actual-expected)))
        if not np.all(np.isfinite(actual)) or error>0.002 or int(np.argmax(actual))!=int(np.argmax(expected)):
            raise AssertionError(f'RTL full logits mismatch pos={pos} error={error}')
        next_token=int(np.argmax(expected)); errors.append(error); outputs.append(next_token)
    match=re.search(r'SIM cycles=(\d+) spi_reads=(\d+)',result.stdout)
    if not match: raise AssertionError('RTL completion evidence missing')
    report={'status':'pass','steps':3,'logits_compared':1536,'max_absolute_error':max(errors),
        'argmax':outputs,'cycles':int(match[1]),'spi_reads':int(match[2]),'actual_io':'SPI mode0 plus UART115200, production firmware/ROM',
        'output_sha256':sha(project/'reports/rtl-uart.txt')}
    write_json(project/'reports/rtl-inference.json',report); return report

def verify_project(project: Path) -> dict:
    from .cpu_bench import tool_environment
    project=project.resolve(); started=time.monotonic()
    write_json(project/'reports/simulation.json',{'status':'fail','returncode':1,
        'reason':'New verification run started; previous success cannot be reused if build fails.'})
    build(project)
    manifest=load_manifest(project)
    # Generated ROM/header must be included after building, not before.
    manifest['verification']['additional_inputs']=sorted(set(manifest['verification']['additional_inputs']+
        ['firmware/rope.h','firmware/model_contract.h','firmware/rom.hex']))
    save_manifest(project,manifest)
    inputs=simulation_input_hashes(project,manifest)
    report={'status':'fail','simulator':'Icarus + Verilator + independent NumPy', 'returncode':1,
        'started_at':datetime.now(timezone.utc).isoformat(),'source_hashes':source_hashes(project,manifest),
        'evidence_version':EVIDENCE_VERSION,'verification_inputs':inputs,'verification_kind':'tinystories-fp32',
        'trusted_harness_hashes':trusted_hashes(project),'checks':[]}
    try:
        compiler,runtime,_=tool_environment()
        run([compiler,'-g2012','-s','tb_io','-o','build/io.vvp','rtl/luna_io.v','tb/tb_io.v'],project,name='io-compile')
        io=run([runtime,'build/io.vvp'],project,name='io-simulation')
        if not all(f'CHECK REQ-{i:03} PASS' in io.stdout for i in (1,2,3)):
            raise AssertionError('Incomplete I/O protocol evidence')
        report['cpu']=cpu_verify(project)
        report['native']=native_verify(project)
        report['rtl']=rtl_verify(project)
        if simulation_input_hashes(project,load_manifest(project))!=inputs: raise ValueError('Inputs changed during verification')
        report.update(status='pass',returncode=0,checks=[{'id':f'REQ-{i:03}','status':'pass'} for i in range(1,8)],
                      runtime_output='PASS independent model and full RTL inference')
    except Exception as exc:
        report['error']=str(exc); raise
    finally:
        report['elapsed_seconds']=time.monotonic()-started
        write_json(project/'reports/simulation.json',report)
    return report

def trusted_hashes(project: Path) -> dict:
    files=[Path(__file__),Path(__file__).with_name('riscv.py'),Path(__file__).with_name('cpu_bench.py')]
    return {str(p.name):sha(p) for p in files}|{n:sha(project/n) for n in ['tb/tb_io.v','tb/sim_main.cpp','tb/tiny_sim.v']}

def evidence_valid(project: Path, manifest: dict, report: dict) -> tuple[bool,str]:
    if report.get('verification_kind')!='tinystories-fp32' or report.get('trusted_harness_hashes')!=trusted_hashes(project):
        return False,'TinyStories independent checker changed or missing'
    for part in ('native','cpu','rtl'):
        if report.get(part,{}).get('status')!='pass': return False,f'TinyStories {part} verification incomplete'
    for part in ('native','rtl'):
        error=report[part].get('max_absolute_error')
        if isinstance(error,bool) or not isinstance(error,(int,float)) or not math.isfinite(error) or not 0<=error<=0.002:
            return False,'Non-finite or excessive TinyStories logits error'
    if report['native'].get('logits_compared')!=32768 or report['rtl'].get('logits_compared')!=1536:
        return False,'TinyStories logits comparison incomplete'
    if report['cpu'].get('case_count')!=114 or not report['cpu'].get('wait_states'):
        return False,'CPU wait-state regression incomplete'
    from .riscv import corpus
    cases=report['cpu'].get('cases',[])
    if [c.get('name') for c in cases]!=[c.name for c in corpus(64)] or any(c.get('status')!='pass' for c in cases):
        return False,'CPU wait-state case inventory changed'
    uart=project/'reports/rtl-uart.txt'
    if not uart.is_file() or sha(uart)!=report['rtl'].get('output_sha256'):
        return False,'Full RTL UART evidence missing or changed'
    if report.get('checks')!=[{'id':f'REQ-{i:03}','status':'pass'} for i in range(1,8)]:
        return False,'TinyStories requirements incomplete'
    return True,'independent FP32/ISA/full RTL inference passed; physical deployment is separate'
