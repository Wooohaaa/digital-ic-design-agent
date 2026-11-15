"""Open Lattice iCE40 implementation backend.

This is a complete, license-free place-and-route-and-bitstream flow -- Yosys
``synth_ice40`` grouping, ``nextpnr-ice40`` placement/routing, and ``icepack``
bitstream packing -- that runs on Windows, Linux and macOS. It is the second,
*open* implementation backend beside the vendor Anlogic TD path, which is what
makes the flow vendor-neutral rather than Anlogic-only.

The tools resolve like the Yosys backend: an ``ICFORGE_NEXTPNR`` /
``ICFORGE_ICEPACK`` override, a native executable on ``PATH``, or the portable
YosysHQ ``yowasp`` entry points (``pip install -e ".[openfpga]"``). When any of
them is missing the backend reports ``blocked``; it never fabricates a bitstream.

Honesty contract (see ``docs/backends.md``):

* ``pass`` means the design synthesized, placed, routed and packed, and the
  recorded bitstream hash matches the file on disk for the current inputs.
* Timing numbers come from nextpnr's static analysis of the routed design and
  are a real frequency estimate for the target -- they are not Anlogic STA and
  not a post-route functional simulation.
* Editing any RTL or constraint source invalidates the recorded hashes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .evidence import sha
from .yosys import augmented_path, first_error, run_script, tool_version, write_script, yosys_command

IMPLEMENT_DIR = Path("build") / "implement"


def _find_override(env_name: str, names: tuple[str, ...]) -> str | None:
    override = os.environ.get(env_name)
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return str(candidate)
    env_path = augmented_path()
    scripts = Path(sys.executable).resolve().parent
    for name in names:
        found = shutil.which(name, path=env_path)
        if found:
            return found
        for extension in ("", ".exe", ".cmd", ".bat"):
            candidate = scripts / (name + extension)
            if candidate.is_file():
                return str(candidate)
    return None


def nextpnr_command() -> str | None:
    return _find_override("ICFORGE_NEXTPNR", ("nextpnr-ice40", "yowasp-nextpnr-ice40"))


def icepack_command() -> str | None:
    return _find_override("ICFORGE_ICEPACK", ("icepack", "yowasp-icepack"))


def toolchain() -> dict:
    yosys = yosys_command()
    nextpnr = nextpnr_command()
    icepack = icepack_command()
    missing = []
    if not yosys:
        missing.append("Yosys")
    if not nextpnr:
        missing.append("nextpnr-ice40")
    if not icepack:
        missing.append("icepack")
    reason = None
    if missing:
        reason = ("The open iCE40 backend requires " + ", ".join(missing)
                  + '; install the openfpga extra (pip install -e ".[openfpga]") or put them on PATH.')
    return {"yosys": yosys, "nextpnr": nextpnr, "icepack": icepack,
            "missing": missing, "reason": reason}


def _tool_version(command: str | None, flag: str) -> str | None:
    if not command:
        return None
    try:
        probe = subprocess.run([command, flag], capture_output=True, text=True,
                               timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (probe.stdout + probe.stderr).strip()
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.lower().startswith("preparing to run"):
            return line
    return None


def _parse_utilization(log: str) -> dict:
    resources: dict[str, dict] = {}
    for match in re.finditer(r"Info:\s+([A-Z0-9_]+):\s+(\d+)/\s*(\d+)\s+(\d+)%", log):
        resources[match.group(1)] = {"used": int(match.group(2)),
                                     "available": int(match.group(3)),
                                     "percent": int(match.group(4))}
    return resources


def _parse_timing(log: str) -> dict | None:
    matches = re.findall(r"Max frequency for clock '([^']+)': ([0-9.]+) MHz \((PASS|FAIL) at ([0-9.]+) MHz\)", log)
    if not matches:
        return None
    clock, frequency, verdict, constraint = matches[-1]
    return {"clock": clock, "max_frequency_mhz": float(frequency),
            "constraint_mhz": float(constraint), "pass": verdict == "PASS"}


def _ice40_script(plan: dict, *, netlist: str) -> str:
    return "\n".join([
        f"read_verilog -sv {' '.join(plan['sources'])}",
        f"synth_ice40 -top {plan['top']} -json {netlist}",
    ]) + "\n"


def run(project: Path, plan: dict, *, timeout: int, progress=None) -> dict:
    """Run synth_ice40 -> nextpnr-ice40 -> icepack and return the report body (no writing)."""
    tools = toolchain()
    build = project / IMPLEMENT_DIR
    build.mkdir(parents=True, exist_ok=True)
    netlist_rel = IMPLEMENT_DIR / f"{plan['top']}.json"
    asc_rel = IMPLEMENT_DIR / f"{plan['top']}.asc"
    bin_rel = IMPLEMENT_DIR / f"{plan['top']}.bin"
    report: dict = {
        "status": "blocked", "stages": {
            "synthesis": {"status": "not_run", "artifact": None, "sha256": None, "tool": "Yosys synth_ice40"},
            "place_route": {"status": "not_run", "artifact": None, "sha256": None, "tool": "nextpnr-ice40"},
            "bitstream": {"status": "not_run", "artifact": None, "sha256": None, "tool": "icepack"},
        },
        "timing": None, "utilization": {}, "constraints": {"pins": plan["pcf"]},
        "tool_versions": {"yosys": tool_version(tools["yosys"]),
                          "nextpnr-ice40": _tool_version(tools["nextpnr"], "--version"),
                          "icepack": _tool_version(tools["icepack"], "--help")},
        "commands": {}, "output_tail": "",
        "reason": tools["reason"] or "The open iCE40 backend requires Yosys, nextpnr-ice40 and icepack.",
    }
    if tools["reason"]:
        return report
    outputs = []

    script_rel = write_script(project, "implement", "ice40.ys", _ice40_script(plan, netlist=netlist_rel.as_posix()))
    synth_cmd = tools["yosys"] + [script_rel]
    report["commands"]["synthesis"] = synth_cmd
    if progress:
        progress(f"Synthesizing {plan['top']} for iCE40 via Yosys.")
    returncode, output = run_script(project, synth_cmd, timeout)
    outputs.append(output)
    netlist_path = project / netlist_rel
    if returncode != 0 or not netlist_path.is_file():
        report["stages"]["synthesis"].update(status="fail")
        report["status"], report["reason"] = "error", first_error(output) or "Yosys synth_ice40 failed."
        report["output_tail"] = "\n".join(outputs)[-4000:]
        return report
    report["stages"]["synthesis"].update(status="pass", artifact=netlist_rel.as_posix(), sha256=sha(netlist_path))

    pnr_cmd = [tools["nextpnr"], f"--{plan['nextpnr_device']}", "--package", plan["package"],
               "--json", netlist_rel.as_posix(), "--pcf", plan["pcf"], "--asc", asc_rel.as_posix(),
               "--seed", "1"]
    report["commands"]["place_route"] = pnr_cmd
    if progress:
        progress(f"Placing and routing {plan['top']} on {plan['device']}.")
    returncode, output = run_script(project, pnr_cmd, timeout)
    outputs.append(output)
    asc_path = project / asc_rel
    if returncode != 0 or not asc_path.is_file():
        report["stages"]["place_route"].update(status="fail")
        report["status"], report["reason"] = "error", first_error(output) or "nextpnr-ice40 failed."
        report["output_tail"] = "\n".join(outputs)[-4000:]
        return report
    report["stages"]["place_route"].update(status="pass", artifact=asc_rel.as_posix(), sha256=sha(asc_path))
    report["utilization"] = _parse_utilization(output)
    report["timing"] = _parse_timing(output)

    pack_cmd = [tools["icepack"], asc_rel.as_posix(), bin_rel.as_posix()]
    report["commands"]["bitstream"] = pack_cmd
    if progress:
        progress("Packing the iCE40 bitstream.")
    returncode, output = run_script(project, pack_cmd, timeout)
    outputs.append(output)
    bin_path = project / bin_rel
    if returncode != 0 or not bin_path.is_file() or bin_path.stat().st_size == 0:
        report["stages"]["bitstream"].update(status="fail")
        report["status"], report["reason"] = "error", first_error(output) or "icepack failed."
        report["output_tail"] = "\n".join(outputs)[-4000:]
        return report
    report["stages"]["bitstream"].update(status="pass", artifact=bin_rel.as_posix(), sha256=sha(bin_path))
    report["status"] = "pass"
    report["reason"] = (f"Placed, routed and packed for {plan['device']}"
                        + (f" ({report['utilization'].get('ICESTORM_LC', {}).get('used')} LCs)."
                           if report["utilization"].get("ICESTORM_LC") else "."))
    report["output_tail"] = "\n".join(outputs)[-4000:]
    return report


def artifact_paths(plan: dict) -> dict:
    return {
        "synthesis": (IMPLEMENT_DIR / f"{plan['top']}.json").as_posix(),
        "place_route": (IMPLEMENT_DIR / f"{plan['top']}.asc").as_posix(),
        "bitstream": (IMPLEMENT_DIR / f"{plan['top']}.bin").as_posix(),
    }


def read_report(project: Path) -> dict:
    path = project / "reports" / "implement.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
