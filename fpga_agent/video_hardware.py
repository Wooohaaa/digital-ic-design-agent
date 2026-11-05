"""Terminal capture and reference-backed acceptance of the HX1P35A cube.

JTAG scan execution, receiver lock, a valid static picture, and rotation are
separate observations. None of them is inferred from another one's success.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .evidence import implementation_valid, sha, simulation_valid
from .project import load_manifest
from .quality import read_report
from .terminal import write_json
from .video import wireframe


def _pillow():
    try:
        from PIL import Image, ImageChops
    except ImportError as exc:
        raise RuntimeError('Video acceptance needs Pillow: python -m pip install -e ".[video]"') from exc
    return Image, ImageChops


def find_ffmpeg() -> Path:
    command = shutil.which('ffmpeg')
    if command:
        return Path(command)
    local = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))
    matches = sorted((local/'Microsoft/WinGet/Packages').glob('Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe'))
    if matches:
        return matches[-1]
    raise FileNotFoundError('FFmpeg is required for terminal video capture')


def _dilate(points, radius=2):
    return {(x+dx,y+dy) for x,y in points
            for dx in range(-radius,radius+1) for dy in range(-radius,radius+1)}


@lru_cache(maxsize=1)
def _references():
    # 128 is sufficient: a cube at a+pi has the same set of edges and b.
    return [(angle, wireframe(angle), _dilate(wireframe(angle))) for angle in range(128)]


def inspect_frame(path: Path) -> dict:
    Image, Chops = _pillow()
    with Image.open(path) as source:
        image = source.convert('RGB')
    if image.size != (640,480):
        return {'status':'fail','reason':'frame size differs from 640x480'}
    red,green,blue = image.split()
    threshold = lambda channel, value: channel.point(lambda p: 255 if p>value else 0)
    mask = Chops.multiply(threshold(Chops.subtract(green,red),50), threshold(Chops.subtract(blue,red),50))
    mask = Chops.multiply(mask, threshold(green,90))
    mask = Chops.multiply(mask, threshold(blue,100))
    points = {(i%640,i//640) for i,p in enumerate(mask.tobytes()) if p}
    if not 400 <= len(points) <= 12000:
        return {'status':'fail','reason':'no cube-sized cyan wireframe','cyan_pixels':len(points)}
    expanded = _dilate(points)
    best = (0.0,0,0.0,0.0)
    for angle,expected,dilated in _references():
        precision = len(points & dilated)/len(points)
        recall = len(expected & expanded)/len(expected)
        score = 2*precision*recall/(precision+recall) if precision+recall else 0
        if score>best[0]+1e-9:
            best = score,angle,precision,recall
    score,angle,precision,recall = best
    valid = precision >= .65 and recall >= .65 and score >= .72
    return {'status':'pass' if valid else 'fail','cyan_pixels':len(points),
            'reference_angle':angle,'reference_precision':round(precision,4),
            'reference_recall':round(recall,4),'reference_score':round(score,4)}


def analyze_frames(paths: list[Path]) -> dict:
    rows = [{'file':str(path),'sha256':sha(path),**inspect_frame(path)} for path in paths]
    passed = [row for row in rows if row['status']=='pass']
    # Coarse pose buckets reject compression noise around one static pose.
    poses = {row['reference_angle']//8 for row in passed}
    valid = len(rows)>=6 and len(passed)>=max(6,.9*len(rows)) and len(poses)>=3
    return {'status':'pass' if valid else 'fail','sample_count':len(rows),
            'valid_frames':len(passed),'distinct_pose_buckets':len(poses),'frames':rows,
            'reason':'independent cube geometry and changing poses matched' if valid else
                     'stable rotating cube was not established; no-signal, black, bars and static frames cannot pass'}


def _preflight(project: Path):
    manifest = load_manifest(project)
    if manifest.get('verification',{}).get('kind') != 'hdmi-cube':
        raise ValueError('Hardware video acceptance requires the hdmi-cube profile')
    for name,validator in [('simulation',simulation_valid),('td',implementation_valid)]:
        valid,reason=validator(project,manifest,read_report(project/f'reports/{name}.json'))
        if not valid:
            raise ValueError(f'{name} gate: {reason}')
    td=read_report(project/'reports/td.json')
    programming=read_report(project/'reports/programming.json')
    if programming.get('status')!='pass' or programming.get('bitstream_hashes')!=td['bitstream_hashes']:
        raise ValueError('Programming evidence is absent or refers to another bitstream')
    payload=programming.get('configuration_payload',{})
    if not payload.get('sha256') or not programming.get('svf_file'):
        raise ValueError('Programming report predates payload binding; program the verified image again')
    from .programmer import validate_svf_payload
    svf=(project/programming['svf_file']).resolve()
    if project not in svf.parents or sha(svf)!=programming.get('svf_sha256'):
        raise ValueError('Programmed SVF evidence is missing or changed')
    bit=project/next(iter(td['bitstream_hashes']))
    if validate_svf_payload(bit,svf)!=payload:
        raise ValueError('Programmed configuration payload changed')
    return td['bitstream_hashes'],programming


def _hardware_run(project: Path, *, device='Hagibis', seconds=12, settle=5, program=False) -> dict:
    project=project.resolve()
    if not 8<=seconds<=60 or not 3<=settle<=seconds-3:
        raise ValueError('Capture needs 8..60 seconds and at least 3 seconds of settling plus 3 seconds of observations')
    if not device or any(c in device for c in '\r\n\x00'):
        raise ValueError('Invalid video device name')
    _pillow();ffmpeg=find_ffmpeg()
    if program:
        from .programmer import program_sram
        program_sram(project)
    hashes,programming=_preflight(project)
    started=datetime.now(timezone.utc)
    directory=project/'reports/hardware-video'/f'{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}'
    directory.mkdir(parents=True,exist_ok=False)
    report={'status':'running','profile':'hx1p35a-hdmi-cube','started_at':started.isoformat(),
            'device':device,'capture_seconds':seconds,'settle_seconds':settle,
            'bitstream_hashes':hashes,'programming_report_sha256':sha(project/'reports/programming.json'),
            'programming_finished_at':programming['finished_at'],
            'verifier_sha256':{'video_hardware.py':sha(Path(__file__)),'video.py':sha(Path(__file__).with_name('video.py'))}}
    target=project/'reports/hardware-video.json';write_json(target,report)
    video=directory/'capture.mkv'
    try:
        if os.name!='nt':raise RuntimeError('This capture profile currently requires Windows DirectShow')
        command=[str(ffmpeg),'-hide_banner','-loglevel','warning','-f','dshow','-video_size','640x480',
                 '-framerate','30','-vcodec','mjpeg','-i',f'video={device}','-t',str(seconds),'-c:v','copy','-y',str(video)]
        result=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=seconds+30)
        (directory/'capture.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode or not video.is_file():raise RuntimeError('FFmpeg capture failed; see capture.log')
        extract=[str(ffmpeg),'-hide_banner','-loglevel','error','-i',str(video),'-vf',
                 f'fps=2,select=gte(t\,{settle})','-y',str(directory/'frame-%03d.png')]
        decoded=subprocess.run(extract,capture_output=True,text=True,errors='replace',timeout=60)
        if decoded.returncode:raise RuntimeError('FFmpeg decode failed: '+decoded.stderr[-2000:])
        paths=sorted(directory.glob('frame-*.png'))
        analysis=analyze_frames(paths)
        for row in analysis['frames']:row['file']=Path(row['file']).relative_to(project).as_posix()
        report.update(analysis)
        report['artifacts']={f.relative_to(project).as_posix():sha(f) for f in [video,*paths]}
        # A concurrent reprogramming or source change invalidates this capture.
        _preflight(project)
        if sha(project/'reports/programming.json')!=report['programming_report_sha256']:
            raise RuntimeError('Board programming changed during capture')
    except Exception as exc:
        report.update(status='fail',error=str(exc));raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat();write_json(target,report)
    return report


def hardware_run(project: Path, *, device='Hagibis', seconds=12, settle=5, program=False) -> dict:
    from .hardware_lock import board_lease
    with board_lease():
        return _attempt(project,device=device,seconds=seconds,settle=settle,program=program)


def _attempt(project: Path, *, device, seconds, settle, program) -> dict:
    project=project.resolve();target=project/'reports/hardware-video.json'
    write_json(target,{'status':'running','profile':'hx1p35a-hdmi-cube',
                      'started_at':datetime.now(timezone.utc).isoformat()})
    try:
        return _hardware_run(project,device=device,seconds=seconds,settle=settle,program=program)
    except Exception as exc:
        report=read_report(target)
        report.update(status='fail',error=str(exc),finished_at=datetime.now(timezone.utc).isoformat())
        write_json(target,report)
        raise


def evidence_valid(project: Path, report: dict) -> tuple[bool,str]:
    try:
        hashes,programming=_preflight(project.resolve())
        if report.get('status')!='pass' or report.get('profile')!='hx1p35a-hdmi-cube' or report.get('bitstream_hashes')!=hashes:
            return False,'Video acceptance is absent, failed, or belongs to another bitstream'
        if report.get('programming_report_sha256')!=sha(project/'reports/programming.json'):
            return False,'Board was reprogrammed since capture'
        if datetime.fromisoformat(report['started_at']) < datetime.fromisoformat(programming['finished_at']):
            return False,'Capture predates the current programming operation'
        for name in ('video_hardware.py','video.py'):
            if report.get('verifier_sha256',{}).get(name)!=sha(Path(__file__).with_name(name)):
                return False,'Video acceptance checker changed'
        artifacts=report.get('artifacts',{})
        if not artifacts: return False,'Captured evidence artifacts missing'
        for name,digest in artifacts.items():
            path=(project/name).resolve()
            if project.resolve() not in path.parents or sha(path)!=digest:
                return False,'Captured evidence artifact missing or changed'
        paths=[project/row['file'] for row in report.get('frames',[])]
        if not paths or any(row['file'] not in artifacts for row in report['frames']):
            return False,'Frame inventory does not match capture evidence'
        actual=analyze_frames(paths)
        if actual['status']!='pass': return False,actual['reason']
        return True,'Physical capture matches independent cube geometry with multiple changing poses'
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
        return False,str(exc)
