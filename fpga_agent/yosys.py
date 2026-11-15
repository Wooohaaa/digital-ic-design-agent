"""Shared Yosys backend discovery and script execution.

The logic-equivalence / property checks (:mod:`fpga_agent.formal`) and the
vendor-neutral synthesis flow (:mod:`fpga_agent.synth`) run the same Yosys
executor and reuse the same hashed-evidence conventions. Backend resolution is
identical in both: an ``ICFORGE_YOSYS`` override, a native ``yosys`` on
``PATH``, a bundled ``.tools/yosys`` tree, or the portable ``yowasp-yosys``
entry point. When none is present callers report ``blocked`` instead of
pretending success.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .evidence import sha
from .terminal import write_json

PROBE_TIMEOUT = 120


def augmented_path() -> str:
    bundled = Path(__file__).resolve().parent.parent / ".tools" / "yosys" / "bin"
    base = os.environ.get("PATH", "")
    if bundled.is_dir():
        return str(bundled) + os.pathsep + base
    return base


def find(*names: str) -> str | None:
    env_path = augmented_path()
    scripts = Path(sys.executable).resolve().parent
    for name in names:
        executable = shutil.which(name, path=env_path)
        if executable:
            return executable
        for suffix in ("", ".exe", ".cmd", ".bat"):
            candidate = scripts / (name + suffix)
            if candidate.is_file():
                return str(candidate)
    return None


def yosys_command() -> list[str]:
    """Resolve a Yosys executable, or return ``[]`` when unavailable."""
    override = os.environ.get("ICFORGE_YOSYS")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return [str(candidate)]
    executable = find("yosys", "yowasp-yosys")
    return [executable] if executable else []


def tool_version(command: list[str]) -> str | None:
    if not command:
        return None
    try:
        probe = subprocess.run(command + ["-V"], capture_output=True, text=True,
                               timeout=PROBE_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (probe.stdout + "\n" + probe.stderr).strip()
    return text.splitlines()[0].strip() if text else None


def tool_fingerprint() -> dict:
    command = yosys_command()
    version = tool_version(command)
    if not command or version is None:
        return {"status": "unavailable"}
    return {"status": "available", "version": version,
            "sha256": hashlib.sha256(version.encode("utf-8", errors="replace")).hexdigest()}


def checker_hash(module_file: Path) -> str:
    return sha(module_file)


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()


def absolute_sources(project: Path, names: list[str], *, label: str) -> list[str]:
    """Validate a list of project-relative HDL paths and return them normalized."""
    if not isinstance(names, list) or not names:
        raise ValueError(f"{label} must be a nonempty list of project-relative HDL files")
    result = []
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label} entries must be nonempty strings")
        relative = Path(name.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{label} path must stay inside the project: {name}")
        path = (project / relative).resolve()
        if project.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"{label} file is missing or escapes the project: {name}")
        if path.suffix.lower() not in {".v", ".sv", ".vh", ".svh"}:
            raise ValueError(f"{label} file must be Verilog/SystemVerilog: {name}")
        result.append(relative.as_posix())
    return result


def run_script(project: Path, command: list[str], timeout: int) -> tuple[int | None, str]:
    environment = dict(os.environ)
    environment["PATH"] = augmented_path()
    try:
        result = subprocess.run(command, cwd=project, env=environment, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return 124, text + f"\nYosys backend timed out after {timeout}s.\n"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout + result.stderr


def write_script(project: Path, subdir: str, name: str, text: str) -> str:
    relative = Path("build") / subdir / name
    (project / relative).parent.mkdir(parents=True, exist_ok=True)
    (project / relative).write_text(text, encoding="utf-8", newline="\n")
    return relative.as_posix()


def read_files(files: list[str]) -> str:
    return " ".join(files)


def first_error(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("ERROR:"):
            return stripped[:300]
    return "Yosys reported an error without a message."


def write_report(project: Path, name: str, report: dict, output: str) -> None:
    directory = project / "reports"
    directory.mkdir(exist_ok=True)
    write_json(directory / f"{name}.json", report)
    (directory / f"{name}.log").write_text(output, encoding="utf-8")
