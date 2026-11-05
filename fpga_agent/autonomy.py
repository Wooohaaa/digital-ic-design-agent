"""Persistent, bounded engineering loop with a detached worker and OS-held lease."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

from .terminal import ProcessRunner, Settings, Workspace, evidence, make_plan, read_json, redact, timestamp, write_json
from .context_budget import ContextBudgetError, pack_context, size
from .knowledge import READ_ACTIONS, execute_read
from .agent_backends import BACKENDS, available as available_agents
from .orchestration import Orchestrator, validate_checks, verify_workspace, workspace_gate, source_paths, imported_paths, git as orchestration_git

TERMINAL = {"completed", "cancelled"}
TASK_LOG_BYTES = 20_000_000
STEP_LOG_BYTES = 4_000_000


def pid_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied: conservatively still running.
        code = wintypes.DWORD()
        try:
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def clean(value):
    if isinstance(value, str):
        return redact(value, dict(os.environ))
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


class Lease:
    """Kernel locks release on process death; stale PID files cannot hold a task hostage."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        self.stream.seek(0, 2)
        if not self.stream.tell():
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise ValueError("当前工作区已有长期任务 worker，先暂停该任务。") from None

    def close(self):
        if self.stream.closed:
            return
        self.stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TaskStore:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.root = workspace.state / "tasks"
        self.lock = workspace.state / "task-worker.lock"

    def directory(self, task_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", task_id):
            raise ValueError("任务 ID 格式无效。")
        return self.root / task_id

    def busy(self) -> bool:
        # A running worker consults its own task list while holding this lease.
        # On Windows, reacquiring its own byte-range lock through a second file
        # handle can be reported inconsistently and may release the original
        # process-scoped lock when that handle closes. The persisted owner plus
        # a live matching PID is authoritative for this short circuit.
        owner = read_json(self.workspace.state / "task-worker.json", {})
        if isinstance(owner, dict) and isinstance(owner.get("id"), str):
            try:
                active = read_json(self.directory(owner["id"]) / "task.json", {})
            except ValueError:
                active = {}
            if (isinstance(active, dict) and active.get("status") in {"queued", "running"}
                    and active.get("pid") == owner.get("pid") and pid_alive(owner.get("pid"))):
                return True
        # Windows can briefly retain an msvcrt byte-range lock after
        # subprocess.wait() observes process exit. Retry a bounded window so
        # a just-dead worker does not look active for one extra task launch.
        deadline = time.monotonic() + (0.05 if os.name == 'nt' else 0.0)
        while True:
            try:
                lease = Lease(self.lock)
            except ValueError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True
                time.sleep(min(0.005, remaining))
                continue
            lease.close()
            return False

    def get(self, task_id: str = "latest") -> dict:
        if task_id == "latest":
            paths = sorted(self.root.glob("*/task.json"), key=lambda p: p.parent.name)
            values = [read_json(path, {}) for path in paths]
            values = [v for v in values if isinstance(v, dict) and v.get("id")]
            if not values:
                raise ValueError("没有长期任务。")
            return self.get(max(values, key=lambda v: v.get("created_at", ""))["id"])
        path = self.directory(task_id) / "task.json"
        for attempt in range(8):
            data = read_json(path)
            if isinstance(data, dict):
                break
            if attempt < 7:
                time.sleep(0.01)
        if not isinstance(data, dict) or data.get("id") != task_id:
            raise ValueError("任务不存在或记录损坏。")
        heartbeat = read_json(self.directory(task_id) / "heartbeat.json", {})
        if heartbeat.get("pid") == data.get("pid") and isinstance(heartbeat.get("elapsed_seconds"), (int, float)):
            data["elapsed_seconds"] = max(data["elapsed_seconds"], heartbeat["elapsed_seconds"])
            data["heartbeat"] = heartbeat.get("time")
        if data.get("status") == "running":
            owner = read_json(self.workspace.state / "task-worker.json", {})
            if not self.busy() or owner.get("id") != task_id:
                data.update(status="interrupted", reason="worker 已退出，恢复时将重新检查磁盘证据。")
        return data

    def list(self) -> list[dict]:
        values = []
        for path in self.root.glob("*/task.json"):
            try:
                value = self.get(path.parent.name)
                values.append(value)
            except ValueError:
                continue
        return sorted(values, key=lambda v: v.get("created_at", ""), reverse=True)

    def save(self, task: dict):
        task["updated_at"] = timestamp()
        write_json(self.directory(task["id"]) / "task.json", clean(task))

    def create(self, objective: str, *, project: str = "", target: str = "delivery",
               settings: Settings | None = None, max_steps: int = 64,
               max_hours: float = 24, max_failures: int = 3, context_chars: int = 96000,
               mode: str = 'engineer', executors: list[str] | None = None, max_agents: int = 3,
               agent_models: dict | None = None, agent_timeout: int = 3600, checks: list | None = None) -> dict:
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 60000:
            raise ValueError("任务目标不能为空，且不能超过 60,000 字符。")
        if target not in {"simulation", "delivery", "workspace"}:
            raise ValueError("验收目标必须为 simulation、delivery 或 workspace。")
        if mode not in {'engineer','orchestrate'}:
            raise ValueError('模式须为 engineer 或 orchestrate。')
        executors = list(dict.fromkeys(BACKENDS if executors is None else executors))
        if not executors or any(name not in BACKENDS for name in executors):
            raise ValueError('执行者只能选择 codex、claude、opencode。')
        if type(max_agents) is not int or not 1<=max_agents<=8 or type(agent_timeout) is not int or not 1<=agent_timeout<=28800:
            raise ValueError('并发 agent 须为 1..8，分工超时须为 1..28,800 秒。')
        if target=='workspace' and mode!='orchestrate':
            raise ValueError('workspace 验收用于 orchestrate 模式。')
        accepted_checks = validate_checks(checks) if target=='workspace' else []
        agent_models=agent_models or {}
        if not isinstance(agent_models,dict) or any(k not in BACKENDS or not isinstance(v,str) or len(v)>200 for k,v in agent_models.items()):
            raise ValueError('执行者模型映射无效。')
        if mode=='orchestrate':
            if Path(orchestration_git(self.workspace.root,'rev-parse','--show-toplevel').decode().strip()).resolve()!=self.workspace.root:
                raise ValueError('编排工作区必须为 Git 仓库根目录。')
            orchestration_git(self.workspace.root,'rev-parse','HEAD')
        if type(max_steps) is not int or not 1 <= max_steps <= 10000:
            raise ValueError("max_steps 必须为 1..10000。")
        if not isinstance(max_hours, (int, float)) or isinstance(max_hours, bool) or not 0 < max_hours <= 720:
            raise ValueError("max_hours 必须大于 0 且不超过 720。")
        if type(max_failures) is not int or not 1 <= max_failures <= 10:
            raise ValueError("max_failures 必须为 1..10。")
        if type(context_chars) is not int or not 16000 <= context_chars <= 2_000_000:
            raise ValueError("context_chars 必须为 16,000..2,000,000。")
        settings = settings or self.workspace.settings()
        if settings.provider not in {"api", "chat", "codex"}:
            raise ValueError("长期任务需要 api、chat 或 codex 实时模型后端；exchange 用于单次响应导入。")
        project = self.workspace.resolve(project).relative_to(self.workspace.root).as_posix() if project else ""
        task = {"version": 1, "id": uuid.uuid4().hex[:12], "objective": objective.strip(),
                "project": project, "projects": [project] if project else [], "target": target, "settings": asdict(settings),
                "status": "queued", "reason": "等待 worker", "created_at": timestamp(),
                "limits": {"steps": max_steps, "seconds": max_hours * 3600, "failures": max_failures, "context_chars":context_chars},
                "iterations": 0, "elapsed_seconds": 0.0, "steps": [], "plan": [], "steering": [],
                "planner_failures": 0, "memory": "", "loaded_skills": []}
        task.update(mode=mode,checks=accepted_checks,delegations=[],orchestration={
            'enabled':mode=='orchestrate','executors':executors,'max_parallel':max_agents,
            'models':agent_models,'job_timeout_seconds':agent_timeout})
        self.save(task)
        return task

    def control(self, task_id: str, action: str) -> dict:
        task = self.get(task_id)
        if action not in {"pause", "cancel"}:
            raise ValueError("不支持的控制操作。")
        if task["status"] in TERMINAL:
            raise ValueError("任务已结束。")
        pids=list(task.get('child_pids',{}).values())+[task.get('child_pid')]
        if not self.busy() and any(pid_alive(pid) for pid in pids):
            raise ValueError('worker 已退出但工具子进程仍运行，请等待或在终端终止后再控制任务：'+str([p for p in pids if pid_alive(p)]))
        owner = read_json(self.workspace.state / "task-worker.json", {})
        if self.busy() and owner.get("id") == task["id"]:
            write_json(self.directory(task["id"]) / "control.json", {"action": action})
        else:
            task.update(status="paused" if action == "pause" else "cancelled", reason="用户请求" )
            self.save(task)
        return self.get(task["id"])

    def steer(self, task_id: str, message: str):
        task = self.get(task_id)
        if task["status"] in TERMINAL or not message.strip() or len(message) > 12000:
            raise ValueError("任务已结束，或补充内容为空/超过 12,000 字符。")
        inbox = self.directory(task["id"]) / "inbox"
        pending = sum(len(read_json(p, {}).get("message", "")) for p in inbox.glob("*.json"))
        if sum(len(n["message"]) for n in task["steering"]) + pending + len(message) > 60000:
            raise ValueError("累计补充要求超过 60,000 字符，请整理成新的任务目标后启动。")
        write_json(self.directory(task["id"]) / "inbox" / f"{time.time_ns()}-{uuid.uuid4().hex}.json",
                   {"message": clean(message), "created_at": timestamp()})

    def launch(self, task_id: str, env: dict | None = None, *, extra_steps: int = 0,
               extra_hours: float = 0, context_chars: int | None = None) -> dict:
        with Lease(self.workspace.state / "task-launch.lock"):
            return self._launch(task_id, env, extra_steps=extra_steps, extra_hours=extra_hours, context_chars=context_chars)

    def _launch(self, task_id: str, env: dict | None = None, *, extra_steps: int = 0,
                extra_hours: float = 0, context_chars: int | None = None) -> dict:
        if self.busy():
            raise ValueError("当前工作区已有长期任务运行。")
        for previous in self.list():
            pids=list(previous.get('child_pids',{}).values())+[previous.get('child_pid')]
            if any(pid_alive(pid) for pid in pids):
                raise ValueError(f"任务 {previous['id']} 的工具子进程仍运行，不能重叠启动。")
        task = self.get(task_id)
        if task["status"] in TERMINAL:
            raise ValueError("任务已完成或取消，需新建目标。")
        if type(extra_steps) is not int or extra_steps < 0 or not math.isfinite(extra_hours) or extra_hours < 0:
            raise ValueError("追加预算必须为非负数。")
        if pid_alive(task.get("child_pid")):
            raise ValueError(f"中断前的工具子进程 {task['child_pid']} 仍运行，不能重叠执行。")
        if context_chars is not None:
            if type(context_chars) is not int or not 16000 <= context_chars <= 2_000_000:
                raise ValueError("context_chars 必须为 16,000..2,000,000。")
            task['limits']['context_chars'] = context_chars
        task["limits"]["steps"] += extra_steps
        task["limits"]["seconds"] += extra_hours * 3600
        if task["iterations"] >= task["limits"]["steps"] or task["elapsed_seconds"] >= task["limits"]["seconds"]:
            raise ValueError("预算已用尽，resume 时追加 --extra-steps 或 --extra-hours。")
        environment = dict(os.environ if env is None else env)
        if task["settings"]["provider"] in {"api", "chat"} and not (environment.get("OPENAI_API_KEY") or environment.get("FPGA_AGENT_API_KEY")):
            raise ValueError("请先通过 OPENAI_API_KEY 或 FPGA_AGENT_API_KEY 提供实时模型凭据。")
        if task["settings"]["provider"] == "codex" and not shutil.which("codex", path=environment.get("PATH")):
            raise ValueError("未找到本机 codex CLI。")
        directory = self.directory(task["id"])
        (directory / "control.json").unlink(missing_ok=True)
        task.update(status="queued", reason="正在启动 worker")
        task["planner_failures"] = 0
        task["resume_iteration"] = task["iterations"]
        self.save(task)
        environment.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        package = str(Path(__file__).resolve().parent.parent)
        environment["PYTHONPATH"] = package + os.pathsep + environment.get("PYTHONPATH", "")
        if task["settings"].get("base_url"):
            environment["OPENAI_BASE_URL"] = task["settings"]["base_url"]
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with (directory / "worker.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-u", "-m", "fpga_agent.autonomy",
                str(self.workspace.root), task["id"]], cwd=self.workspace.root, env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **flags)
        threading.Thread(target=process.wait, daemon=True).start()
        task_path = directory / "task.json"
        # Read the persisted state directly here. get() deliberately converts a
        # stale `running` record to `interrupted` when its lease is already
        # released; during fast Windows worker shutdown that can race with the
        # final state read and make a successful task look like a launch error.
        for _ in range(60):
            current = read_json(task_path, {})
            returncode = process.poll()
            if returncode is not None:
                # The worker persists its terminal state before releasing the
                # OS lease. Give an atomic replace a short bounded settle window
                # before classifying an exited process as an unexpected crash.
                for _ in range(10):
                    if current.get("status") not in {"queued", "running"}:
                        return current
                    time.sleep(0.01)
                    current = read_json(task_path, {})
                if current.get("status") not in {"queued", "running"}:
                    return current
                if current.get("pid") == process.pid:
                    reason = "worker 已退出，恢复时将重新检查磁盘证据；请查看任务目录的 worker.log。"
                else:
                    reason = f"worker 启动失败（退出码 {returncode}），请查看任务目录的 worker.log。"
                current.update(status="interrupted", reason=reason)
                self.save(current)
                return current
            if current.get("pid") == process.pid and current.get("status") == "running":
                return current
            time.sleep(0.05)
        current = read_json(task_path)
        return current if isinstance(current, dict) else self.get(task["id"])


def completion_gate(workspace: Workspace, task: dict) -> tuple[bool, str]:
    if task.get('mode')=='orchestrate' and not task.get('delegations'):
        return False,'编排任务尚无完成的外部分工。'
    if any(batch['status']!='integrated' for batch in task.get('delegations',[])):
        return False,'存在未完成的外部分工批次，不能仅凭最后一份报告完成。'
    if task.get('target')=='workspace':
        return workspace_gate(workspace.root,task)
    projects = list(dict.fromkeys(task.get("projects") or ([task["project"]] if task.get("project") else [])))
    if len(projects) > 1:
        outcomes = []
        for name in projects:
            valid, reason = completion_gate(workspace, {**task, "projects": [], "project": name})
            if not valid:
                return False, name + "：" + reason
            outcomes.append(name)
        return True, "全部工程的当前验收门槛通过：" + ", ".join(outcomes)
    if projects:
        task = {**task, "project": projects[0]}
    if not task.get("project"):
        return False, "未指定已验收工程。"
    project = workspace.resolve(task["project"])
    state = evidence(project)
    if state["simulation"] != "current":
        return False, "仿真证据尚未通过或已过期。"
    if task["target"] == "simulation":
        return True, "当前输入的仿真验收通过。"
    if state["implementation"] != "current":
        return False, "实现证据尚未通过或已过期。"
    from .evidence import input_hashes, sha, simulation_input_hashes
    manifest = read_json(project / "project.json", {})
    artifact = project / "deliverables" / f"{manifest.get('project')}-delivery.zip"
    try:
        if artifact.with_suffix(".zip.sha256").read_text(encoding="utf-8").split()[0] != sha(artifact):
            return False, "交付 ZIP 指纹不匹配。"
        with zipfile.ZipFile(artifact) as archive:
            delivery = json.loads(archive.read("delivery.json"))
            td = read_json(project / "reports/td.json", {})
            if delivery.get("kind") != "implemented" or delivery.get("inputs") != input_hashes(project, manifest):
                return False, "交付包对应的工程输入已过期。"
            if delivery.get("bitstreams") != td.get("bitstream_hashes"):
                return False, "交付位流证据已过期。"
            verified = simulation_input_hashes(project, manifest)
            archived_sim = json.loads(archive.read("reports/simulation.json"))
            if archived_sim.get("verification_inputs") != verified:
                return False, "包内仿真报告对应的输入已过期。"
            for name, digest in verified.items():
                if not name.startswith("@") and hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    return False, "包内验证输入指纹不匹配。"
            for name, digest in delivery["bitstreams"].items():
                if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                    return False, "包内位流指纹不匹配。"
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, zipfile.BadZipFile):
        return False, "缺少有效交付包或其校验记录。"
    return True, "当前输入的仿真、FPGA 实现及交付包通过；工程签核范围见交付报告。"


class TaskEngine:
    def __init__(self, store: TaskStore, task_id: str):
        self.store, self.workspace = store, store.workspace
        self.task = store.get(task_id)
        self.directory = store.directory(self.task["id"])
        self.base_elapsed = self.task["elapsed_seconds"]
        self.started = time.monotonic()
        self.runner: ProcessRunner | None = None
        self.tail: list[str] = []

    def save(self):
        self.task["elapsed_seconds"] = round(self.base_elapsed + time.monotonic() - self.started, 3)
        self.task["heartbeat"] = timestamp()
        for old in self.task["steps"][:-16]:
            write_json(self.directory / f"step-{old['iteration']:04d}" / "result.json", clean(old))
        self.task["steps"] = self.task["steps"][-16:]
        self.store.save(self.task)

    def heartbeat(self):
        write_json(self.directory / "heartbeat.json", {"pid": os.getpid(), "time": timestamp(),
            "elapsed_seconds": round(self.base_elapsed + time.monotonic() - self.started, 3)})

    def save_step(self, step):
        write_json(self.directory / f"step-{step['iteration']:04d}" / "result.json", clean(step))

    def event(self, kind: str, **values):
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(clean({"time": timestamp(), "kind": kind, **values}), ensure_ascii=False) + "\n")

    def output(self, line: str):
        line = redact(line, dict(os.environ))
        self.tail = "\n".join(self.tail + [line])[-8000:].splitlines()[-20:]
        log = self.directory / "output.log"
        if not log.exists() or log.stat().st_size < TASK_LOG_BYTES:
            with log.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        elif not self.task.get('output_log_truncated'):
            self.task['output_log_truncated'] = True
            with log.open('a',encoding='utf-8') as stream:
                stream.write('\n[ICFORGE_LOG_LIMIT] 汇总日志达到上限；后续输出查看各步骤日志或工具原生报告。\n')
        if self.task['iterations']:
            step_log = self.directory/f"step-{self.task['iterations']:04d}"/'output.log'
            step_log.parent.mkdir(exist_ok=True)
            if not step_log.exists() or step_log.stat().st_size < STEP_LOG_BYTES:
                with step_log.open('a',encoding='utf-8') as stream:
                    stream.write(line+'\n')
            elif self.task['steps'] and not self.task['steps'][-1].get('log_truncated'):
                self.task['steps'][-1]['log_truncated'] = True
                with step_log.open('a',encoding='utf-8') as stream:
                    stream.write('\n[ICFORGE_LOG_LIMIT] 步骤日志达到上限；最终输出尾部仍保存在 result.json。\n')

    def stop_reason(self):
        control = read_json(self.directory / "control.json", {})
        if control.get("action") in {"pause", "cancel"}:
            return ("paused" if control["action"] == "pause" else "cancelled", "用户请求")
        if self.base_elapsed + time.monotonic() - self.started >= self.task["limits"]["seconds"]:
            return "limited", "运行时间预算已用尽。"
        return None

    async def command(self, argv: list[str]) -> int:
        self.runner = ProcessRunner()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
        if self.task["settings"].get("base_url"):
            env["OPENAI_BASE_URL"] = self.task["settings"]["base_url"]
        def spawned(pid):
            self.task["child_pid"] = pid
            self.save()
        running = asyncio.create_task(self.runner.run(argv, self.workspace.root, env, self.output, spawned))
        last_save = time.monotonic()
        try:
            while not running.done():
                await asyncio.wait({running}, timeout=0.25)
                if self.stop_reason():
                    await self.runner.cancel()
                if time.monotonic() - last_save >= 1:
                    self.heartbeat()
                    last_save = time.monotonic()
            return await running
        finally:
            if not running.done():
                await self.runner.cancel()
                await running
            self.runner = None
            self.task.pop("child_pid", None)
            self.save()

    def consume_steering(self):
        # Also called before dispatching waiting children. Persist before deleting the inbox note.
        for path in sorted((self.directory / "inbox").glob("*.json")):
            note = read_json(path, {})
            if path.name not in {n.get("id") for n in self.task["steering"]} and isinstance(note.get("message"), str):
                self.task["steering"].append({"id": path.name, **note})
                self.save()
            path.unlink(missing_ok=True)

    def context(self):
        self.consume_steering()
        selected = self.workspace.resolve(self.task["project"]) if self.task["project"] else None
        context = self.workspace.context(selected, self.task["objective"])
        history = []
        for step in self.task["steps"][-8:]:
            compact = {k: step[k] for k in ("iteration", "status", "exit_code", "reason", "log_truncated") if k in step}
            compact["output_tail"] = ["\n".join(step.get("output_tail", []))[-8000:]]
            if step.get("decision"):
                compact["action"] = step["decision"].get("action")
                compact["message"] = step["decision"].get("message", "")[:2000]
            history.append(compact)
        context.update(objective=self.task["objective"], acceptance_target=self.task["target"],
                       plan=self.task["plan"], memory=self.task.get("memory", ""), steering=self.task["steering"],
                       history=history, remaining_iterations=self.task["limits"]["steps"] - self.task["iterations"])
        context["task_projects"] = [{"project": name, "evidence": evidence(self.workspace.resolve(name))}
                                    for name in self.task.get("projects", [])]
        current_skills = {item['id']:item['sha256'] for item in context.get('skill_catalog',{}).get('skills',[])}
        context['loaded_skills'] = [item if current_skills.get(item['id'])==item['sha256'] else
            {'id':item['id'],'sha256':item['sha256'],'current_sha256':current_skills.get(item['id']),
             'stale':True,'note':'技能内容已变化或消失，请重新加载后使用。'} for item in self.task.get('loaded_skills',[])]
        if self.task['steps']:
            previous = self.task['steps'][-1]
            context['last_observation'] = previous.get('observation', {key:previous[key] for key in ('iteration','status','exit_code','output_tail','log_truncated') if key in previous})
            if previous.get('decision',{}).get('action') == 'skill' and previous.get('observation',{}).get('id') in {item['id'] for item in self.task.get('loaded_skills',[])}:
                context['last_observation'] = {key:previous['observation'][key] for key in ('id','sha256','source') if key in previous['observation']}
        context['experience_catalog'] = [{key:task.get(key) for key in ('id','status','updated_at')}
            | {'objective':task.get('objective','')[:200]} for task in self.store.list()[:6] if task['id']!=self.task['id']]
        context['orchestration']={**self.task.get('orchestration',{'enabled':False}),
            'available_agents':available_agents() if self.task.get('mode')=='orchestrate' else [],
            'batches':self.task.get('delegations',[])[-4:],'checks':self.task.get('checks',[]),
            'active_batch':self.task.get('active_delegation','')}
        if self.task.get('mode')=='orchestrate':
            paths=source_paths(self.workspace.root,imported_paths(self.task))
            context['workspace_files']={'paths':paths[:500],'total':len(paths),'omitted':max(0,len(paths)-500)}
        return clean(context)

    async def run(self):
        lease = Lease(self.store.lock)
        try:
            write_json(self.workspace.state / "task-worker.json", {"id": self.task["id"], "pid": os.getpid()})
            # A killed worker leaves uncertain operations; replan from disk, never assume success.
            for step in self.task["steps"]:
                if step["status"] in {"planning", "running"}:
                    step.update(status="interrupted", reason="前一个 worker 中断，需重新检查证据。")
                    self.save_step(step)
            self.task.update(status="running", reason="持续执行中", pid=os.getpid())
            self.save()
            self.event("started")
            if self.task.get('active_delegation') and self.task.get('mode')=='orchestrate':
                resumed=await Orchestrator(self).run([],self.task['active_delegation'])
                if self.task['steps']:
                    step=self.task['steps'][-1]
                    step.update(observation=resumed,status='interrupted' if self.stop_reason() else 'succeeded' if resumed['status']=='integrated' else 'failed')
                    self.save_step(step)
                self.save()
            while True:
                stopped = self.stop_reason()
                if stopped:
                    self.task.update(status=stopped[0], reason=stopped[1])
                    break
                if self.task["iterations"] >= self.task["limits"]["steps"]:
                    self.task.update(status="limited", reason="决策步数预算已用尽。")
                    break
                original_context = self.context()
                try:
                    context, report = pack_context(original_context, self.task['limits'].get('context_chars',96000))
                except ContextBudgetError as exc:
                    self.task.update(status='blocked',reason=str(exc),context_budget={'error':str(exc),
                        'limit_chars':self.task['limits'].get('context_chars',96000),'before_chars':size(original_context)})
                    write_json(self.directory/'context-budget.json',self.task['context_budget'])
                    break
                self.task['context_budget'] = report
                write_json(self.directory/'context-budget.json',report)
                self.task["iterations"] += 1
                iteration = self.task["iterations"]
                directory = self.directory / f"step-{iteration:04d}"
                directory.mkdir(exist_ok=True)
                if report['compacted']:
                    write_json(directory/'context-full.json',original_context)
                request = directory / "request.json"
                write_json(request, {"workspace": str(self.workspace.root), "provider": self.task["settings"]["provider"],
                                     "model": self.task["settings"]["model"], "context": context})
                step = {"iteration": iteration, "status": "planning", "started_at": timestamp()}
                self.task["steps"].append(step)
                self.tail = []
                self.save()
                code = await self.command([sys.executable, "-u", "-m", "fpga_agent.task_planner", str(request)])
                if self.stop_reason():
                    step.update(status="interrupted", output_tail=self.tail)
                    continue
                payload = read_json(directory / "decision.json") if code == 0 else None
                try:
                    if code:
                        detail = '\n'.join(self.tail)
                        budget = self.task['limits'].get('context_chars',96000)
                        if re.search(r'context_length_exceeded|prompt_too_long|prompt (?:is )?too long|maximum context length|context window exceeded|too many input tokens',detail,re.I) and budget > 16000:
                            self.task['limits']['context_chars'] = max(16000,int(budget*0.7))
                            step['adjusted_context_chars'] = self.task['limits']['context_chars']
                            self.event('context_reduced',reason='provider reported context overflow',previous=budget,current=self.task['limits']['context_chars'])
                        raise ValueError("模型请求失败：" + "\n".join(self.tail[-3:])[:2000])
                    if not isinstance(payload, dict) or payload.get("decision") not in {"execute", "complete", "blocked"}:
                        raise ValueError("模型未返回有效的长期任务决策。")
                    if not isinstance(payload.get("message"), str) or not isinstance(payload.get("plan"), list) or not all(isinstance(p, str) for p in payload["plan"]):
                        raise ValueError("模型计划格式无效。")
                    if not isinstance(payload.get("memory"), str) or len(payload["memory"]) > 12000:
                        raise ValueError("工作记忆必须是最多 12,000 字符的文本。")
                    self.task["memory"] = payload["memory"]
                    self.task["plan"] = payload["plan"][:30]
                    compact_arguments = dict(payload.get("arguments", {})) if isinstance(payload.get("arguments"), dict) else {}
                    if isinstance(compact_arguments.get("brief"), str):
                        compact_arguments["brief"] = compact_arguments["brief"][:2000]
                    step.update(decision=clean({"decision": payload["decision"], "action": payload.get("action"),
                        "message": payload["message"][:2000], "arguments": compact_arguments}), status="running")
                    if payload["decision"] == "blocked":
                        step["status"] = "blocked"
                        self.task.update(status="blocked", reason=payload["message"])
                        break
                    if payload["decision"] == "complete":
                        valid, reason = completion_gate(self.workspace, self.task)
                        step.update(status="succeeded" if valid else "failed", output_tail=[reason])
                        if valid:
                            self.task.update(status="completed", reason=payload["message"], completion_evidence=reason)
                            break
                        raise ValueError("完成声明被验收门禁拒绝：" + reason)
                    selected = self.workspace.resolve(self.task["project"]) if self.task["project"] else None
                    settings = Settings(**self.task["settings"])
                    local_read = payload['action'] in READ_ACTIONS
                    delegated = payload['action'] in {'delegate','workspace-verify'}
                    if not isinstance(payload.get('arguments'),dict):
                        raise ValueError('工具参数必须为对象。')
                    plan = None if local_read or delegated else make_plan(self.workspace, settings, payload["action"], payload["arguments"], selected)
                    if plan and plan.project:
                        self.task["project"] = plan.project.relative_to(self.workspace.root).as_posix()
                        if self.task["project"] not in self.task.setdefault("projects", []):
                            self.task["projects"].append(self.task["project"])
                    elif payload["action"] == "suite":
                        for path in sorted((self.workspace.root / "benchmarks").glob("rv32_*/project.json")):
                            name = path.parent.relative_to(self.workspace.root).as_posix()
                            if name not in self.task.setdefault("projects", []):
                                self.task["projects"].append(name)
                    stable = {key: original_context.get(key) for key in ("source_index", "manifest", "evidence")}
                    fingerprint = hashlib.sha256(json.dumps([payload["action"], payload["arguments"], stable], sort_keys=True).encode()).hexdigest()
                    step["fingerprint"] = fingerprint
                    previous = [s for s in self.task["steps"][:-1] if s["iteration"] > self.task.get("resume_iteration", 0)]
                    if sum(s.get("fingerprint") == fingerprint for s in previous[-self.task["limits"]["failures"]:]) >= self.task["limits"]["failures"]:
                        step["status"] = "blocked"
                        self.task.update(status="blocked", reason="重复相同工具且工程证据没有进展，请补充要求或修复阻塞项。")
                        break
                    self.task["planner_failures"] = 0
                    self.tail = []
                    self.event("tool_started", iteration=iteration, action=payload["action"])
                    if delegated:
                        if self.task.get('mode')!='orchestrate':
                            raise ValueError('外部 agent 分工须选择 orchestrate 模式。')
                        if payload['action']=='delegate':
                            result=await Orchestrator(self).run(payload['arguments'].get('jobs',[]),payload['arguments'].get('batch_id',''))
                            code=0 if result['status']=='integrated' else 1
                        else:
                            result=await verify_workspace(self)
                            code=0 if result['status']=='pass' else 1
                        step.update(observation=clean(result),exit_code=code,output_tail=self.tail)
                    elif local_read:
                        result = execute_read(self.workspace, selected, payload['action'], payload['arguments'], task_id=self.task['id'])
                        if payload['action'] == 'skill' and result.get('content') and not result.get('resource'):
                            loaded = self.task.setdefault('loaded_skills', [])
                            self.task['loaded_skills'] = [item for item in loaded if item['id']!=result['id']] + [result]
                        if payload['action'] == 'unload-skill':
                            loaded = self.task.get('loaded_skills', [])
                            if result['unload'] not in {item['id'] for item in loaded}:
                                raise ValueError('该技能尚未加载，请指定已加载技能的完整 ID。')
                            self.task['loaded_skills'] = [item for item in loaded if item['id']!=result['unload']]
                        step['observation'] = clean(result)
                        self.output(json.dumps(clean(result),ensure_ascii=False))
                        code = 0
                        step.update(exit_code=0, output_tail=self.tail)
                    else:
                        for index, argv in enumerate(plan.commands):
                            step["command_index"] = index
                            self.save()
                            code = await self.command(argv)
                            step.update(exit_code=code, output_tail=self.tail)
                            if self.stop_reason() or code:
                                break
                            step["completed_commands"] = index + 1
                            self.save()
                    step["status"] = "interrupted" if self.stop_reason() else "succeeded" if code == 0 else "failed"
                    self.event("tool_finished", iteration=iteration, status=step["status"], exit_code=code)
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    step.update(status="failed", output_tail=(self.tail + [redact(str(exc))])[-20:])
                    self.task["planner_failures"] += 1
                    if self.task["planner_failures"] >= self.task["limits"]["failures"]:
                        self.task.update(status="blocked", reason="连续规划/验收失败：" + redact(str(exc)))
                        break
                    # Bounded retries remain interruptible, and use the same cumulative budget.
                    for _ in range(2 ** self.task["planner_failures"] * 4):
                        if self.stop_reason():
                            break
                        await asyncio.sleep(0.25)
                step["finished_at"] = timestamp()
                self.save_step(step)
                self.save()
        except BaseException as exc:
            self.task.update(status="interrupted", reason=redact(f"worker 中断：{type(exc).__name__}: {exc}"))
            raise
        finally:
            try:
                if self.task["steps"]:
                    self.task["steps"][-1].setdefault("finished_at", timestamp())
                    self.save_step(self.task["steps"][-1])
                self.save()
                self.event("stopped", status=self.task["status"], reason=self.task["reason"])
            finally:
                lease.close()
        return self.task


def configure_parser(commands):
    task = commands.add_parser("task", help="持久主动任务：后台规划、执行、验收、暂停与恢复")
    sub = task.add_subparsers(dest="task_command", required=True)
    start = sub.add_parser("start", help="在后台启动目标，关闭终端仍继续运行")
    start.add_argument("objective")
    start.add_argument("--workspace", default=".")
    start.add_argument("--project", default="")
    start.add_argument("--provider", choices=("api", "chat", "codex"))
    start.add_argument("--model")
    start.add_argument("--target", choices=("simulation", "delivery", "workspace"), default="delivery")
    start.add_argument('--mode',choices=('engineer','orchestrate'),default='engineer')
    start.add_argument('--agents',default='codex,claude,opencode')
    start.add_argument('--max-agents',type=int,default=3)
    start.add_argument('--agent-model',action='append',default=[],metavar='BACKEND=MODEL')
    start.add_argument('--agent-timeout',type=int,default=3600)
    start.add_argument('--checks-file',type=Path)
    start.add_argument("--max-steps", type=int, default=64)
    start.add_argument("--max-hours", type=float, default=24)
    start.add_argument("--max-failures", type=int, default=3)
    start.add_argument('--context-chars',type=int,default=96000)
    for name in ("list", "status", "logs", "context", "agents", "pause", "resume", "cancel", "steer"):
        parser = sub.add_parser(name)
        parser.add_argument("--workspace", default=".")
        if name != "list":
            parser.add_argument("task_id", nargs="?", default="latest")
        if name == "resume":
            parser.add_argument("--extra-steps", type=int, default=0)
            parser.add_argument("--extra-hours", type=float, default=0)
            parser.add_argument("--use-current-model", action="store_true")
            parser.add_argument('--context-chars',type=int)
        if name == "steer":
            parser.add_argument("message")


def dispatch(args) -> int:
    store = TaskStore(Workspace(Path(args.workspace)))
    name = args.task_command
    if name == "start":
        if store.busy():
            raise ValueError("当前工作区已有长期任务运行。")
        settings = store.workspace.settings()
        if args.provider:
            settings.provider = args.provider
        if args.model:
            settings.model = args.model
        checks=None
        if args.target=='workspace':
            if args.checks_file:
                checks=read_json(store.workspace.resolve(str(args.checks_file)))
            elif (store.workspace.root/'tests').is_dir():
                checks=[{'name':'unittest','argv':['python','-m','unittest','discover','-s','tests','-v']}]
            else:
                raise ValueError('workspace 目标请用 --checks-file 声明验收命令。')
        models={name:value for name in BACKENDS if (value:=getattr(settings,'agent_'+name+'_model'))}
        for mapping in args.agent_model:
            name,separator,model=mapping.partition('=')
            if not separator or not model:
                raise ValueError('--agent-model 用法：codex=模型名。')
            models[name]=model
        task = store.create(args.objective, project=args.project or (settings.project if args.target!='workspace' else ''), target=args.target, settings=settings,
                            max_steps=args.max_steps, max_hours=args.max_hours, max_failures=args.max_failures, context_chars=args.context_chars,
                            mode=args.mode,executors=args.agents.split(','),max_agents=args.max_agents,agent_models=models,
                            agent_timeout=args.agent_timeout,checks=checks)
        result = store.launch(task["id"])
    elif name == "list":
        result = [{key: task.get(key) for key in ("id", "objective", "status", "reason", "project", "iterations", "elapsed_seconds")}
                  for task in store.list()]
    elif name == "resume":
        if args.use_current_model:
            if store.busy():
                raise ValueError("先暂停当前任务再更新模型。")
            task = store.get(args.task_id)
            settings = store.workspace.settings()
            if settings.provider not in {"api", "chat", "codex"}:
                raise ValueError("持续任务需要实时模型后端。")
            task["settings"] = asdict(settings)
            store.save(task)
        result = store.launch(args.task_id, extra_steps=args.extra_steps, extra_hours=args.extra_hours, context_chars=args.context_chars)
    elif name in {"pause", "cancel"}:
        result = store.control(args.task_id, name)
        result["requested_action"] = name
    elif name == "steer":
        store.steer(args.task_id, args.message)
        result = {"message": "补充要求已保存，将在下一次决策前读取。"}
    elif name == "logs":
        task = store.get(args.task_id)
        path = store.directory(task["id"]) / "output.log"
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 32000))
                print(stream.read().decode("utf-8", errors="replace"))
        return 0
    elif name == 'context':
        result = store.get(args.task_id).get('context_budget',{'note':'该任务尚未发送规划请求。'})
    elif name == 'agents':
        result=store.get(args.task_id).get('delegations',[])
    else:
        result = store.get(args.task_id)
    print(json.dumps(clean(result), ensure_ascii=False, indent=2))
    return 1 if name in {"start", "resume"} and result.get("status") == "interrupted" else 0


def main():
    try:
        store = TaskStore(Workspace(Path(sys.argv[1])))
        asyncio.run(TaskEngine(store, sys.argv[2]).run())
        return 0
    except (ValueError, OSError) as exc:
        print(redact(str(exc)), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
