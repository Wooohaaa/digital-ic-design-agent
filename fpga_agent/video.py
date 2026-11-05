"""Host-owned differential checks for the fixed HX1P35A HDMI cube contract."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

from .evidence import EVIDENCE_VERSION, source_hashes, simulation_input_hashes, sha, implementation_valid
from .project import load_manifest
from .terminal import timestamp, write_json


def geometry(angle: int) -> list[tuple[int,int]]:
    """Independent integer specification, also checked against a floating-point rotation."""
    a=angle*math.tau/256; b=((2*angle+32)%256)*math.tau/256
    sa,ca,sb,cb=[round(256*x) for x in (math.sin(a),math.cos(a),math.sin(b),math.cos(b))]
    result=[]
    for index in range(8):
        x,y,z=[64 if index&(1<<bit) else -64 for bit in range(3)]
        xr=(x*ca+z*sa)//256; zr=(-x*sa+z*ca)//256; yr=(y*cb-zr*sb)//256
        pos=(320+(xr*3)//2,240-(yr*3)//2)
        float_x=320+1.5*(x*math.cos(a)+z*math.sin(a))
        float_y=240-1.5*(y*math.cos(b)-(-x*math.sin(a)+z*math.cos(a))*math.sin(b))
        if max(abs(pos[0]-float_x),abs(pos[1]-float_y))>5:
            raise AssertionError('reference numerical bound exceeded')
        result.append(pos)
    return result


def wireframe(angle: int) -> set[tuple[int,int]]:
    vertices=geometry(angle); pixels=set()
    for index in range(8):
        for bit in range(3):
            other=index^(1<<bit)
            if other<index:continue
            x,y=vertices[index]; ex,ey=vertices[other]
            dx=abs(ex-x); sx=1 if x<ex else -1
            dy=-abs(ey-y); sy=1 if y<ey else -1; err=dx+dy
            while True:
                pixels.add((x,y))
                if (x,y)==(ex,ey):break
                doubled=2*err
                if doubled>=dy:err+=dy;x+=sx
                if doubled<=dx:err+=dx;y+=sy
    return pixels


def tmds(data: int, de: bool, control: int, disparity: int) -> tuple[int,int]:
    if not de:
        return (0b1101010100,0b0010101011,0b0101010100,0b1010101011)[control],0
    invert=data.bit_count()>4 or (data.bit_count()==4 and not data&1)
    bits=[data&1]
    for bit in range(1,8):bits.append(bits[-1]^((data>>bit)&1)^int(invert))
    q=sum(value<<bit for bit,value in enumerate(bits)); q8=int(not invert)
    balance=2*q.bit_count()-8
    if disparity==0 or balance==0:
        return ((1-q8)<<9)|(q8<<8)|(q if q8 else q^255),disparity+(balance if q8 else -balance)
    if (disparity>0 and balance>0) or (disparity<0 and balance<0):
        return (1<<9)|(q8<<8)|(q^255),disparity+2*q8-balance
    return (q8<<8)|q,disparity+balance-2*(1-q8)


class VideoChecks:
    def __init__(self,project: Path):
        self.project=project.resolve();self.directory=self.project/'build/operator'
        self.directory.mkdir(parents=True,exist_ok=True)
        root=Path(__file__).resolve().parent.parent
        bundled=root/'.tools/icarus/bin'
        self.compiler=str(bundled/'iverilog.exe') if (bundled/'iverilog.exe').is_file() else shutil.which('iverilog')
        self.runtime=str(bundled/'vvp.exe') if (bundled/'vvp.exe').is_file() else shutil.which('vvp')
        if not self.compiler or not self.runtime:raise ValueError('Icarus Verilog is required')
        self.env=dict(os.environ,PYTHONIOENCODING='utf-8')
        self.env['PATH']=str(Path(self.compiler).parent)+os.pathsep+self.env.get('PATH','')
        self.rows=[]

    def board(self,manifest):
        board=json.loads((self.project/'board/hx1p35a.json').read_text(encoding='utf-8'))
        if manifest.get('device')!='PH1P35MDG324' or manifest.get('top_module')!='hdmi_cube_top':
            raise ValueError('wrong board device/top module')
        adc=(self.project/manifest['board_constraints']).read_text(encoding='utf-8')
        found={}
        for port,body in re.findall(r'set_pin_assignment\s*\{\s*([^}]+?)\s*\}\s*\{([^}]+)\}',adc,re.I):
            props=dict((k.upper(),v.strip()) for k,v in re.findall(r'(\w+)\s*=\s*([^;]+);',body))
            if port in found:raise ValueError('duplicate pin assignment: '+port)
            found[port]=props
        for port,signal in board['signals'].items():
            if found.get(port,{}).get('LOCATION')!=signal['pin'] or found.get(port,{}).get('IOSTANDARD')!=signal['io_standard']:
                raise ValueError('pin/electrical standard differs from verified schematic: '+port)
            partner=signal.get('differential_partner')
            if partner:
                if signal['io_standard']!='TMDS33' or port.endswith('_n'):
                    raise ValueError('TMDS differential outputs must use a P-side port with an implicit paired N pad: '+port)
                if any(other.get('LOCATION')==partner for name,other in found.items() if name!=port):
                    raise ValueError('paired TMDS N pad must not be assigned as a second top-level port: '+partner)
        sdc=(self.project/manifest['timing_constraints']).read_text(encoding='utf-8')
        if not re.search(r'create_clock[^\n]*-period\s+20(?:\.0*)?\b',sdc):raise ValueError('missing 50 MHz input constraint')
        required_pll_clocks=(
            ('hdmi_pixel_clk',r'-divide_by\s+2(?:\.0+)?\b',r'u_hdmi_clocks/u_hdmi_pll\.clkc\[4\]'),
            ('hdmi_serial_clk',r'-multiply_by\s+2\.5(?:0+)?\b',r'u_hdmi_clocks/u_hdmi_pll\.clkc\[5\]'),
        )
        for name,ratio,target in required_pll_clocks:
            matches=[line for line in sdc.splitlines() if re.search(r'create_generated_clock[^\n]*-name\s+'+name+r'\b',line)]
            if len(matches)!=1 or not re.search(ratio,matches[0]) or not re.search(target,matches[0]):
                raise ValueError(f'missing or incorrect generated clock constraint: {name}')
        reset_exceptions=[line for line in sdc.splitlines() if 'set_false_path' in line and 'pixel_reset_request' in line]
        if len(reset_exceptions)!=1 or '-setup' not in reset_exceptions[0] or 'sys_clk_50m' not in reset_exceptions[0] or '-through' not in reset_exceptions[0] or 'get_nets' not in reset_exceptions[0]:
            raise ValueError('the async reset crossing must be constrained only through its request net')
        if re.search(r'set_false_path[^\n]*-to\s+\[get_clocks|set_clock_groups[^\n]*-asynchronous',sdc):
            raise ValueError('related HDMI clocks cannot be blanket false-pathed/asynchronous')
        for source in manifest['rtl_sources']:
            if re.search(r'(?m)^\s*`define\s+ICFORGE_SIM\b',(self.project/source).read_text(encoding='utf-8')):
                raise ValueError('behavioral simulation branch must not be enabled in hardware')
        self.rows.append({'id':'board','status':'pass'})

    def run(self,name: str,files: list[str],tb: str,timeout=180):
        bench=self.directory/(name+'.sv');binary=self.directory/(name+'.vvp')
        bench.write_text(tb,encoding='utf-8')
        argv=[self.compiler,'-g2012','-DICFORGE_SIM','-s','tb','-o',str(binary),
              *[str(self.project/'rtl'/file) for file in files],str(bench)]
        compiled=subprocess.run(argv,cwd=self.project,env=self.env,capture_output=True,text=True,errors='replace',timeout=60)
        log=compiled.stdout+compiled.stderr
        if compiled.returncode:
            (self.directory/(name+'.log')).write_text(log,encoding='utf-8')
            raise ValueError(name+' compile failed: '+log[-4000:])
        started=time.monotonic()
        result=subprocess.run([self.runtime,str(binary)],cwd=self.project,env=self.env,capture_output=True,text=True,errors='replace',timeout=timeout)
        log+=result.stdout+result.stderr;(self.directory/(name+'.log')).write_text(log,encoding='utf-8')
        if result.returncode or result.stdout.count('OPERATOR_PASS')!=1:
            raise ValueError(name+' failed: '+log[-4000:])
        self.rows.append({'id':name,'status':'pass','seconds':round(time.monotonic()-started,3)})
        return result.stdout

    def timing(self):
        return self.run('timing',['video_timing.v'],'''
`timescale 1ns/1ps
module tb;
reg clk=0,reset=1;always #5 clk=~clk;
wire[9:0]x,y;wire de,hs,vs,fs;
video_timing dut(clk,reset,x,y,de,hs,vs,fs);
integer n,ex,ey;
initial begin
repeat(2)begin @(posedge clk);#1;end reset=0;
for(n=0;n<840020;n=n+1)begin
ex=n%800;ey=(n/800)%525;
if(x!==ex || y!==ey || de!==((ex<640)&&(ey<480)) || hs!==!((ex>=656)&&(ex<752)) || vs!==!((ey>=490)&&(ey<492)) || fs!==((ex==0)&&(ey==480)))
$fatal(1,"raster mismatch n=%0d x=%0d y=%0d hs=%b vs=%b",n,x,y,hs,vs);
@(posedge clk);#1;
end
$display("OPERATOR_PASS");$finish;end
endmodule
''')

    def encoder(self):
        rng=random.Random(47051);vectors=[];rd=0
        seq=[(0,False,c) for c in range(4)]
        seq += [(d,True,0) for _ in range(8) for d in range(256)]
        seq += [(rng.randrange(256),rng.random()>.12,rng.randrange(4)) for _ in range(3000)]
        for data,de,c in seq:
            symbol,rd=tmds(data,de,c,rd)
            if abs(rd)>8:raise AssertionError('reference disparity out of range')
            vectors.append((data<<13)|(int(de)<<12)|(c<<10)|symbol)
        path=self.directory/'encoder.hex';path.write_text('\n'.join(f'{v:06x}' for v in vectors),encoding='ascii')
        return self.run('tmds',['tmds_encoder.v'],f'''
`timescale 1ns/1ps
module tb;
reg clk=0,reset=1;always #5 clk=~clk;
// Establish a real input transition before the first vector; do not depend on time-zero @* scheduling.
reg[7:0]data=8'ha5;reg de=0,c0=0,c1=0;wire[9:0]symbol;
reg[20:0]vectors[0:{len(vectors)-1}];integer i;
tmds_encoder dut(clk,reset,data,de,c0,c1,symbol);
initial begin
$readmemh("{path.as_posix()}",vectors);
repeat(2)begin @(posedge clk);#1;end reset=0;
for(i=0;i<{len(vectors)};i=i+1)begin
data=vectors[i][20:13];de=vectors[i][12];c1=vectors[i][11];c0=vectors[i][10];
@(posedge clk);#1;
if(symbol!==vectors[i][9:0])$fatal(1,"TMDS mismatch i=%0d data=%h got=%h expected=%h",i,data,symbol,vectors[i][9:0]);
end
reset=1;@(posedge clk);#1;reset=0;data=0;de=0;c0=0;c1=0;@(posedge clk);#1;
if(symbol!==10'b1101010100)$fatal(1,"TMDS reset recovery");
$display("OPERATOR_PASS");$finish;end
endmodule
''')

    def vertices(self):
        output=self.run('geometry',['cube_vertices.v'],'''
module tb;
reg[7:0]angle=0;wire[79:0]vertices_x,vertices_y;integer a,i;
cube_vertices dut(angle,vertices_x,vertices_y);
initial begin
for(a=0;a<256;a=a+1)begin angle=a;#10;
for(i=0;i<8;i=i+1)begin
if((^vertices_x===1'bx)||(^vertices_y===1'bx))$fatal(1,"unknown geometry");
$display("POSE %0d %0d %0d %0d",a,i,vertices_x[i*10+:10],vertices_y[i*10+:10]);end
end $display("OPERATOR_PASS");$finish;end
endmodule
''')
        rows=[tuple(map(int,line.split()[1:])) for line in output.splitlines() if line.startswith('POSE ')]
        if len(rows)!=2048:raise ValueError('geometry pose inventory incomplete')
        for angle,index,x,y in rows:
            expected=geometry(angle)[index]
            if (x,y)!=expected:raise ValueError(f'geometry angle={angle} vertex={index}: {(x,y)} != {expected}')

    def framebuffer(self,poses=(0,1,2)):
        if not poses or any(type(p) is not int or not 0<=p<256 for p in poses) or len(set(poses))!=len(poses):
            raise ValueError('capture poses must be unique angles in 0..255')
        directory=self.directory.as_posix()
        capture=' || '.join(f'frame=={pose}' for pose in poses)
        self.run('framebuffer',['cube_vertices.v','cube_framebuffer.v'],f'''
`timescale 1ns/1ps
module tb;
reg clk=0,reset=1,frame_start=0;always #5 clk=~clk;
reg[9:0]x=0,y=0;wire[79:0]vx,vy;wire[7:0]angle;wire pixel_on,busy;
cube_vertices geom(angle,vx,vy);
cube_framebuffer dut(clk,reset,frame_start,x,y,vx,vy,angle,pixel_on,busy);
integer frame,n,xx,yy,fileid;reg[4095:0]filename;
initial begin
repeat(3)begin @(posedge clk);#1;end reset=0;
// Early frame boundary while the back buffer is incomplete cannot reveal uninitialized RAM.
frame_start=1;@(posedge clk);#1;frame_start=0;
if(angle!==0 || pixel_on!==0)$fatal(1,"premature swap / uninitialized front");
for(frame=0;frame<={max(poses)};frame=frame+1)begin
n=0;while(busy!==0 && n<420000)begin @(posedge clk);#1;n=n+1;end
if(n>=420000)$fatal(1,"rendering exceeded one frame");
@(negedge clk);frame_start=1;@(posedge clk);#1;frame_start=0;
if(angle!==((frame+1)&255))$fatal(1,"frame angle cadence");
if({capture})begin
$sformat(filename,"{directory}/frame-%02d.bits",frame);fileid=$fopen(filename,"w");
if(fileid==0)$fatal(1,"cannot create captured frame file");
for(yy=0;yy<480;yy=yy+1)begin
for(xx=0;xx<640;xx=xx+1)begin
@(negedge clk);x=xx;y=yy;@(posedge clk);#1;
if(pixel_on!==0 && pixel_on!==1)$fatal(1,"uninitialized displayed pixel");
$fwrite(fileid,"%b",pixel_on);end
$fwrite(fileid,"\\n");end
$fclose(fileid);
@(negedge clk);x=900;y=600;@(posedge clk);#1;
if(pixel_on!==0)$fatal(1,"out-of-range pixel");
end
end
reset=1;repeat(3)begin @(posedge clk);#1;end
if(pixel_on!==0 || angle!==0)$fatal(1,"framebuffer reset recovery");
$display("OPERATOR_PASS");$finish;end
endmodule
''',timeout=240)
        metrics=[]
        for angle in poses:
            lines=(self.directory/f'frame-{angle:02d}.bits').read_text().splitlines()
            if len(lines)!=480 or any(len(row)!=640 or set(row)-set('01') for row in lines):raise ValueError('incomplete captured frame')
            actual={(x,y) for y,line in enumerate(lines) for x,bit in enumerate(line) if bit=='1'}
            expected=wireframe(angle)
            def nearby(point,pixels):
                x,y=point;return any((x+dx,y+dy) in pixels for dx in (-1,0,1) for dy in (-1,0,1))
            if not actual or any(not nearby(p,expected) for p in actual) or any(not nearby(p,actual) for p in expected):
                raise ValueError(f'frame {angle} differs from all-12-edge reference: actual={len(actual)} expected={len(expected)}')
            ppm=self.directory/f'frame-{angle:02d}.ppm'
            rgb=bytes(component for line in lines for bit in line for component in ((40,220,255) if bit=='1' else (0,0,0)))
            ppm.write_bytes(b'P6\n640 480\n255\n'+rgb)
            metrics.append({'angle':angle,'lit_pixels':len(actual),'bitmap_sha256':hashlib.sha256(''.join(lines).encode()).hexdigest()})
        if len({m['bitmap_sha256'] for m in metrics})!=len(poses):raise ValueError('cube is not rotating')
        return metrics

    def serializer(self):
        directory=self.directory.as_posix()
        self.run('serializer',['hdmi_phy.v'],f'''
`timescale 1ps/1ps
module tb;
reg pc=0,sc=0,reset=1;always #20 pc=~pc;always #4 sc=~sc;
reg[9:0]b=0,g=0,r=0;wire[2:0]dp;wire cp;
hdmi_serializer dut(pc,sc,reset,b,g,r,dp,cp);
integer i,samples,fileid;reg recording=0;
always @(posedge sc or negedge sc)begin #1;
if(recording)begin
if((^dp)===1'bx || cp===1'bx)$fatal(1,"unknown P-side TMDS output");
$fwrite(fileid,"%0t %h %b\\n",$time,dp,cp);end end
initial begin
fileid=$fopen("{directory}/serial.txt","w");
repeat(4)@(negedge pc);reset=0;recording=1;
for(i=0;i<128;i=i+1)begin @(negedge pc);
b=(i*37+19)&1023;g=(i*71+263)&1023;r=(i*109+587)&1023;end
repeat(3)@(negedge pc);recording=0;$fclose(fileid);
$display("OPERATOR_PASS");$finish;end
endmodule
''')
        samples=[line.split() for line in (self.directory/'serial.txt').read_text().splitlines()]
        if len(samples)<1280:raise ValueError('serializer capture incomplete')
        expected=[tuple((i*factor+offset)&1023 for factor,offset in ((37,19),(71,263),(109,587))) for i in range(128)]
        matched=False
        for phase in range(10):
            words=[]
            for base in range(phase,len(samples)-9,10):
                words.append(tuple(sum(((int(samples[base+bit][1],16)>>lane)&1)<<bit for bit in range(10)) for lane in range(3)))
            for start in range(8):
                if words[start:start+100]==expected[4:104]:matched=True
        if not matched:raise ValueError('serializer word boundary/bit order/lane order mismatch')
        clocks=[int(row[2]) for row in samples]
        # Ignore the reset-release fill latency of the four registered ODDR lanes.
        steady=clocks[40:-40]
        if len(steady)<200 or any(steady[i]!=steady[i+10] for i in range(len(steady)-10)) or not 0.45<sum(steady)/len(steady)<.55:
            raise ValueError('forwarded clock is not a stable 50% pixel clock')
        self.hardware_serializer()

    def hardware_serializer(self):
        """Exercise the synthesizable branch with a single-edge-input DDR model.

        UG1310 section 28 and ph1p_logic_hrio.v define d0 as rising-edge
        output data and d1 as falling-edge output data. Both halves of the
        fabric pair must be captured before the upstream NBA register update.
        This functional model does not substitute for vendor timing simulation.
        """
        directory=self.directory.as_posix();bench=self.directory/'serializer-hardware.sv'
        binary=self.directory/'serializer-hardware.vvp';capture=self.directory/'serial-hardware.txt'
        bench.write_text(f'''
`timescale 1ps/1ps
module PH1P_LOGIC_ODDR(input wire clk,input wire rst,input wire d1,input wire d0,output reg q);
reg falling_half;
always @(posedge clk or posedge rst) begin
if(rst) begin q<=1'b0;falling_half<=1'b0;end
else begin q<=d0;falling_half<=d1;end
end
always @(negedge clk or posedge rst) if(rst) q<=1'b0; else q<=falling_half;
endmodule
module tb;
reg pc=0,sc=0,reset=1;always #4000 sc=~sc;
integer phase;
initial begin
if(!$value$plusargs("phase=%d",phase))phase=0;
#(phase);forever #20000 pc=~pc;
end
reg[9:0]b=0,g=0,r=0;wire[2:0]dp;wire cp;
hdmi_serializer dut(pc,sc,reset,b,g,r,dp,cp);
integer i,j,fileid;reg recording=0;
always @(posedge sc or negedge sc)begin #1;if(recording)$fwrite(fileid,"%h %b\\n",dp,cp);end
initial begin
fileid=$fopen("{capture.as_posix()}","w");if(fileid==0)$fatal(1,"capture open");
repeat(5)@(negedge pc);reset=0;repeat(5)@(negedge pc);recording=1;
// Registered encoder outputs have independent clock-to-Q/routing delay.
// A serializer must not capture a mixture of two successive pixel symbols.
for(i=0;i<128;i=i+1)begin @(posedge pc);
for(j=0;j<10;j=j+1)begin
b[j]<=#(100+j*100)(((i*37+19)>>j)&1);
g[j]<=#(100+j*100)(((i*71+263)>>j)&1);
r[j]<=#(100+j*100)(((i*109+587)>>j)&1);
end end
repeat(6)@(negedge pc);recording=0;$fclose(fileid);$display("HARDWARE_SERIALIZER_PASS");$finish;end
initial begin #12000000;$fatal(1,"hardware serializer timeout");end
endmodule
''',encoding='utf-8')
        argv=[self.compiler,'-g2012','-s','tb','-o',str(binary),str(self.project/'rtl/hdmi_phy.v'),str(bench)]
        started=time.monotonic()
        compiled=subprocess.run(argv,cwd=self.project,env=self.env,capture_output=True,text=True,errors='replace',timeout=60)
        log=compiled.stdout+compiled.stderr
        if compiled.returncode:
            (self.directory/'serializer-hardware.log').write_text(log,encoding='utf-8')
            raise ValueError('hardware serializer compile failed: '+log[-4000:])
        for phase in range(0,8000,500):
            self._check_hardware_serial_capture(binary,capture,phase,log)
        if self.rows and self.rows[-1].get('id')=='serializer':
            self.rows[-1]['oddr_rtl_simulation']='pass'
            self.rows[-1]['oddr_phase_cases']=16
            self.rows[-1]['hardware_seconds']=round(time.monotonic()-started,3)

    def _check_hardware_serial_capture(self,binary,capture,phase,compile_log):
        result=subprocess.run([self.runtime,str(binary),f'+phase={phase}'],cwd=self.project,env=self.env,capture_output=True,text=True,errors='replace',timeout=30)
        log=compile_log
        log+=result.stdout+result.stderr;(self.directory/'serializer-hardware.log').write_text(log,encoding='utf-8')
        if result.returncode or result.stdout.count('HARDWARE_SERIALIZER_PASS')!=1:
            raise ValueError('hardware serializer failed: '+log[-4000:])
        rows=[line.split() for line in capture.read_text(encoding='ascii').splitlines()]
        if len(rows)<1280:raise ValueError('hardware serializer capture incomplete')
        expected=[tuple((i*factor+offset)&1023 for factor,offset in ((37,19),(71,263),(109,587))) for i in range(128)]
        matched=False
        for phase in range(10):
            words=[]
            for base in range(phase,len(rows)-9,10):
                words.append(tuple(sum(((int(rows[base+bit][0],16)>>lane)&1)<<bit for bit in range(10)) for lane in range(3)))
            for start in range(max(0,len(words)-128)):
                if words[start:start+128]==expected:matched=True;break
            if matched:break
        if not matched:raise ValueError(f'PH1P ODDR symbol capture failed with pixel/serial phase={phase}ps: word boundary, bit order, or mixed symbols')
        clocks=[int(row[1]) for row in rows];steady=clocks[40:-40]
        if len(steady)<200 or any(steady[i]!=steady[i+10] for i in range(len(steady)-10)) or not 0.45<sum(steady)/len(steady)<0.55:
            raise ValueError('PH1P ODDR forwarded clock is not a stable 50% pixel clock')

    def top(self):
        path=self.directory/'top-symbols.txt'
        self.run('top',['video_timing.v','tmds_encoder.v','cube_vertices.v','cube_framebuffer.v','hdmi_phy.v','hdmi_cube_top.v'],f'''
`timescale 1ps/1ps
module tb;
reg clk50=0;always #10000 clk50=~clk50;
wire[2:0]dp;wire cp;
hdmi_cube_top dut(clk50,dp,cp);
integer fileid,wordn,bitn;reg[9:0]blue,green,red;
initial begin
fileid=$fopen("{path.as_posix()}","w");if(fileid==0)$fatal(1,"capture open");
wait(dut.pixel_reset===1'b0);
repeat(1000)@(posedge cp);
for(wordn=0;wordn<842000;wordn=wordn+1)begin
@(posedge cp);#3;
blue=0;green=0;red=0;
for(bitn=0;bitn<10;bitn=bitn+1)begin
if((^dp===1'bx)||cp===1'bx)$fatal(1,"top P-side TMDS/clock output");
blue[bitn]=dp[0];green[bitn]=dp[1];red[bitn]=dp[2];
if(bitn<9)#4000;
end
$fwrite(fileid,"%03h %03h %03h\\n",blue,green,red);
end
$fclose(fileid);$display("OPERATOR_PASS");$finish;
end
initial begin #40000000000;$fatal(1,"top clock/startup timeout");end
endmodule
''',timeout=300)
        words=[tuple(int(v,16) for v in line.split()) for line in path.read_text().splitlines()]
        controls={852:0,171:1,340:2,683:3}
        def ctrl(word):return controls.get(word[0])
        anchor=next((i for i in range(1,len(words)) if ctrl(words[i]) in (0,1) and ctrl(words[i-1]) in (2,3)),None)
        if anchor is None:raise ValueError('top vertical sync absent')
        frames=[]
        for pose in range(3):
            lines=(self.directory/f'frame-{pose:02d}.bits').read_text().splitlines()
            frames.append({(x,y) for y,line in enumerate(lines) for x,bit in enumerate(line) if bit=='1'})
        count=0;lit=0
        def decode(symbol):
            q=(symbol&255)^(255 if symbol&512 else 0);data=q&1
            for bit in range(1,8):data|=((((q>>bit)^(q>>(bit-1)))&1)^int(not symbol&256))<<bit
            return data
        for i,word in enumerate(words):
            raster=i-anchor+490*800
            if raster<0:continue
            x=raster%800;y=(raster//800)%525;frame=raster//420000
            active=x<640 and y<480
            if not active:
                control=int(not(656<=x<752))|(int(not(490<=y<492))<<1)
                if word!=(next(k for k,v in controls.items() if v==control),852,852):
                    raise ValueError(f'top sync/blanking alignment at frame={frame},x={x},y={y}: {word}')
            else:
                rgb=(decode(word[2]),decode(word[1]),decode(word[0]))
                on=frame>0 and frame-1<len(frames) and (x,y) in frames[frame-1]
                if rgb!=((40,220,255) if on else (1,1,1)):
                    raise ValueError(f'top RGB/read/encoder alignment at frame={frame},x={x},y={y}: {rgb}, expected pixel={int(on)}')
                if frame==1:count+=1;lit+=int(on)
        if count!=640*480 or not lit:raise ValueError('top complete rotating-frame capture missing')


def validate_project(project: Path,full: bool=False) -> dict:
    project=project.resolve();manifest=load_manifest(project);started=time.monotonic()
    if manifest.get('verification',{}).get('kind')!='hdmi-cube':
        raise ValueError('project.json must declare verification.kind="hdmi-cube" for the operator-owned profile')
    inputs=simulation_input_hashes(project,manifest)
    checks=VideoChecks(project)
    pending={'status':'running','verification_kind':'hdmi-cube','started_at':timestamp(),
             'trusted_checker_sha256':sha(Path(__file__))}
    write_json(project/'reports/simulation.json',pending)
    try:
        checks.board(manifest)
        checks.timing();checks.encoder();checks.vertices();frames=checks.framebuffer();checks.serializer();checks.top()
    except Exception as exc:
        write_json(project/'reports/simulation.json',{**pending,'status':'fail','error':str(exc),
            'elapsed_seconds':round(time.monotonic()-started,3),'checks':checks.rows})
        raise
    if simulation_input_hashes(project,load_manifest(project))!=inputs:raise ValueError('sources changed during verification')
    report={'status':'pass','verification_kind':'hdmi-cube','simulator':'Icarus Verilog + host differential reference',
            'returncode':0,'started_at':timestamp(),'elapsed_seconds':round(time.monotonic()-started,3),
            'source_hashes':source_hashes(project,manifest),'verification_inputs':inputs,'evidence_version':EVIDENCE_VERSION,
            'trusted_checker_sha256':sha(Path(__file__)),'runtime_output':'OPERATOR_PASS','checks':checks.rows,'frames':frames}
    write_json(project/'reports/simulation.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    if full:
        from .cli import do_td
        if do_td(project,'full',None,update_manifest=False):raise ValueError('native TD implementation failed; inspect reports/td.json')
        td=json.loads((project/'reports/td.json').read_text(encoding='utf-8'))
        if not implementation_valid(project,manifest,td)[0]:raise ValueError('TD evidence gate failed')
        resources=td.get('resource_summary') or {}
        if not resources.get('eram',{}).get('used'):raise ValueError('framebuffers were not mapped to ERAM')
    return report


def evidence_valid(project: Path,manifest: dict,report: dict) -> tuple[bool,str]:
    if report.get('verification_kind')!='hdmi-cube' or report.get('trusted_checker_sha256')!=sha(Path(__file__)):
        return False,'HDMI independent checker missing or changed'
    rows=report.get('checks',[])
    if not isinstance(rows,list) or [row.get('id') for row in rows]!=['board','timing','tmds','geometry','framebuffer','serializer','top'] or any(row.get('status')!='pass' for row in rows):
        return False,'HDMI independent check inventory incomplete'
    serializer=rows[5]
    if serializer.get('oddr_rtl_simulation')!='pass' or serializer.get('oddr_phase_cases')!=16:
        return False,'Synthesizable ODDR branch phase/skew simulation is incomplete'
    frames=report.get('frames',[])
    if not isinstance(frames,list) or len(frames)!=3 or [f.get('angle') for f in frames]!=[0,1,2] or len({f.get('bitmap_sha256') for f in frames})!=3:
        return False,'HDMI rotating frame evidence incomplete'
    return True,'pass'
