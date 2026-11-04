"""Terminal-only SRAM programming with live HX1P35A JTAG chain detection."""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from .bitbang_relay import BitbangRelay
from datetime import datetime, timezone
from pathlib import Path

from .evidence import implementation_valid, sha, simulation_valid
from .project import load_manifest
from .quality import read_report
from .td import _tcl_braced, find_td_command
from .terminal import write_json


def probe_chain(host: str, port: int) -> list[int]:
    """Read up to two reset-selected IDCODE registers without programming."""
    commands=bytearray()
    def cycle(tms,tdi=0,read=False):
        commands.append(48+(int(tms)<<1)+tdi)
        if read:
            commands.extend(b'R')
        commands.append(52+(int(tms)<<1)+tdi)
    for _ in range(8):
        cycle(1)
    for tms in (0,1,0,0):
        cycle(tms)
    for i in range(64):
        cycle(i==63,read=True)
    cycle(1); cycle(0)
    commands.extend(b'0Q')
    with socket.create_connection((host,port),timeout=10) as connection:
        connection.sendall(commands)
        response=b''
        while len(response)<64:
            data=connection.recv(64-len(response))
            if not data:
                break
            response+=data
    if len(response)!=64 or set(response)-set(b'01'):
        raise RuntimeError('Incomplete JTAG IDCODE scan')
    words=[sum((b-48)<<i for i,b in enumerate(response[n:n+32])) for n in (0,32)]
    if words==[0x4808f0e7,0]:
        return words[:1]
    if words==[0x10300a6d,0x4808f0e7]:
        return words
    raise RuntimeError('Unexpected JTAG chain: '+','.join(f'{v:08x}' for v in words))


def program_sram(project: Path, *, host: str = '127.0.0.1', port: int = 5555,
                 _chain_retry: bool = True) -> dict:
    """Generate vendor SRAM SVF, then execute with strict TDO checking.

    Detect PH1P35 IR8 alone or preceded by Nuclei IR5. OpenOCD adds bypass
    bits as needed. The Nuclei TAP may disappear during reconfiguration;
    retry once only when a fresh physical scan proves this transition.
    The existing AL-LINK-FT remote-bitbang server must already be running.
    """
    project = project.resolve()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',host) or not 1 <= port <= 65535:
        raise ValueError('Invalid hardware server address')
    manifest = load_manifest(project)
    for name, validator in [('simulation',simulation_valid),('td',implementation_valid)]:
        valid, reason = validator(project,manifest,read_report(project/f'reports/{name}.json'))
        if not valid:
            raise ValueError(f'{name} gate: {reason}')
    if manifest.get('device') != 'PH1P35MDG324':
        raise ValueError('This programmer profile requires PH1P35MDG324')
    td = read_report(project/'reports/td.json')
    hashes = td.get('bitstream_hashes',{})
    if len(hashes) != 1:
        raise ValueError('Exactly one validated bitstream is required')
    name, digest = next(iter(hashes.items()))
    bit = (project/name).resolve()
    if project not in bit.parents or sha(bit) != digest:
        raise ValueError('Bitstream escaped project or changed')
    executable = shutil.which('openocd') or shutil.which('openocd.exe')
    if not executable:
        fallback = Path(r'C:\msys64\ucrt64\bin\openocd.exe')
        executable = str(fallback) if fallback.is_file() else None
    if not executable:
        raise FileNotFoundError('Install OpenOCD with remote_bitbang support')
    reports = project/'reports'
    reports.mkdir(exist_ok=True)
    result = {'status':'fail','started_at':datetime.now(timezone.utc).isoformat(),
              'mode':'SRAM only','host':host,'port':port,'bitstream_hashes':hashes,
              'chain':[{'idcode':'10300a6d','irlen':5},{'idcode':'4808f0e7','irlen':8}]}
    write_json(reports/'programming.json',result)
    svf = project/'build/program-sram.svf'
    svf.parent.mkdir(exist_ok=True)
    log = ''
    try:
        tool = find_td_command().with_name('bw_commands_prompt.exe')
        script = (f'if {{[catch {{svfgen -scan_mode prog_sram -level 1 -total 1 -bypass 0 '
                  f'-bit {_tcl_braced(bit.as_posix())} -svf {_tcl_braced(svf.as_posix())}}} msg]}} '
                  '{puts "ICFORGE_SVF_ERROR=$msg"} else {puts ICFORGE_SVF_DONE}\nexit\n')
        generated = subprocess.run([str(tool)],input=script,cwd=svf.parent,
                                   capture_output=True,text=True,errors='replace',timeout=90)
        log += generated.stdout+generated.stderr
        if generated.returncode or 'ICFORGE_SVF_DONE' not in log or not svf.is_file():
            raise RuntimeError('Vendor SRAM SVF generation failed')
        actual_chain = probe_chain(host,port)
        result['chain'] = [{'idcode':f'{v:08x}','irlen':5 if v==0x10300a6d else 8} for v in actual_chain]
        commands = [f'adapter driver remote_bitbang',f'remote_bitbang host {host}',
                    f'remote_bitbang port {port}','transport select jtag','adapter speed 1000',
                    *(['jtag newtap nuclei tap -irlen 5 -expected-id 0x10300a6d'] if len(actual_chain)==2 else []),
                    'jtag newtap ph1 tap -irlen 8 -expected-id 0x4808f0e7','init',
                    f'svf -tap ph1.tap {_tcl_braced(svf.as_posix())} quiet progress',
                    'echo ICFORGE_SRAM_DONE','shutdown']
        argv = [executable]
        for command in commands:
            argv.extend(['-c',command])
        env = os.environ.copy()
        env['PATH'] = str(Path(executable).parent)+os.pathsep+env.get('PATH','')
        with BitbangRelay(host,port) as relay:
            index = argv.index(f'remote_bitbang port {port}')
            argv[index] = f'remote_bitbang port {relay.port}'
            argv[argv.index(f'remote_bitbang host {host}')] = 'remote_bitbang host 127.0.0.1'
            programmed = subprocess.run(argv,cwd=svf.parent,env=env,capture_output=True,
                                        text=True,errors='replace',timeout=1200)
        result['relay_bytes'] = relay.forwarded_bytes
        output = programmed.stdout+programmed.stderr
        log += output
        if (programmed.returncode or 'ICFORGE_SRAM_DONE' not in output or
                not re.search(r'svf file programmed successfully for \d+ commands with 0 errors',output)):
            if _chain_retry and actual_chain == [0x10300a6d,0x4808f0e7] and probe_chain(host,port) == [0x4808f0e7]:
                (reports/'programming-chain-transition.txt').write_text(log,encoding='utf-8')
                result=program_sram(project,host=host,port=port,_chain_retry=False)
                result['chain_transition']='Nuclei TAP disappeared during configuration; rescanned and retried once'
                log+='\nCHAIN TRANSITION RETRY\n'+(reports/'programming.txt').read_text(encoding='utf-8')
                return result
            raise RuntimeError('SRAM programming/TDO validation failed; see programming.txt')
        if sha(bit) != digest:
            raise RuntimeError('Bitstream changed during programming')
        result.update(status='pass',svf_sha256=sha(svf),transport='OpenOCD remote_bitbang; vendor SRAM SVF')
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        (reports/'programming.txt').write_text(log,encoding='utf-8')
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(reports/'programming.json',result)
    return result
