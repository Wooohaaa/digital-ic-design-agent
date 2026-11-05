import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fpga_agent.autonomy import Lease, TaskEngine, TaskStore, completion_gate, pid_alive
from fpga_agent.terminal import Settings, TaskPlan, Workspace, read_json, write_json


def decision(action="init", kind="execute", **arguments):
    return {"decision": kind, "message": "下一步检查真实证据", "plan": ["实现", "验证", "交付"], "memory": "保留验证要求",
            "action": action, "arguments": {"project": "demo", "brief": "", "architecture": "multi",
                                              "full": False, "simulation_only": False, **arguments}}


class ScriptedEngine(TaskEngine):
    def __init__(self, store, task_id, decisions, real_tools=False):
        super().__init__(store, task_id)
        self.decisions = iter(decisions)
        self.real_tools = real_tools
        self.tool_calls = 0

    async def command(self, argv):
        if "fpga_agent.task_planner" in argv:
            write_json(Path(argv[-1]).with_name("decision.json"), next(self.decisions))
            return 0
        self.tool_calls += 1
        if self.real_tools:
            return await super().command(argv)
        self.output("synthetic failed tool observation")
        return 2


class PersistentTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.store = TaskStore(self.workspace)

    def create(self, **kwargs):
        return self.store.create("完成 demo 的设计与验证", **kwargs)

    async def test_one_worker_lease_and_path_boundaries(self):
        lease = Lease(self.store.lock)
        try:
            self.assertTrue(self.store.busy())
            with self.assertRaises(ValueError):
                Lease(self.store.lock)
        finally:
            lease.close()
        self.assertFalse(self.store.busy())
        with self.assertRaises(ValueError):
            self.store.directory("../escape")
        with self.assertRaises(ValueError):
            self.create(project="../escape")

    @unittest.skipUnless(os.name == "nt", "msvcrt unlock timing is Windows-specific")
    def test_busy_retries_transient_kernel_unlock_after_process_exit(self):
        class ReleasedLease:
            def close(self):
                pass

        with patch("fpga_agent.autonomy.Lease", side_effect=[ValueError("still releasing"), ReleasedLease()]), patch(
            "fpga_agent.autonomy.time.sleep"
        ) as sleep:
            self.assertFalse(self.store.busy())
        sleep.assert_called_once()

    def test_busy_trusts_a_live_persisted_worker_owner_without_relocking(self):
        task = self.create()
        task.update(status="running", pid=os.getpid())
        self.store.save(task)
        write_json(self.workspace.state / "task-worker.json", {"id": task["id"], "pid": os.getpid()})
        with patch("fpga_agent.autonomy.Lease", side_effect=AssertionError("must not reacquire worker lease")):
            self.assertTrue(self.store.busy())

    async def test_kernel_lease_releases_after_worker_is_forcibly_killed(self):
        script = "from pathlib import Path;from fpga_agent.autonomy import Lease;import sys,time;l=Lease(Path(sys.argv[1]));print('locked',flush=True);time.sleep(30)"
        process = subprocess.Popen([sys.executable, '-u', '-c', script, str(self.store.lock)], stdout=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline().strip(), b'locked')
            self.assertTrue(self.store.busy())
        finally:
            process.kill()
            process.wait(timeout=5)
            process.stdout.close()
        self.assertFalse(self.store.busy())

    async def test_missing_evidence_rejects_model_completion_and_stops(self):
        task = self.create(max_failures=1)
        engine = ScriptedEngine(self.store, task["id"], [decision("none", "complete")])
        result = await engine.run()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("验收", result["reason"])
        self.assertEqual(engine.tool_calls, 0)

    async def test_multi_project_task_cannot_finish_when_only_last_project_passes(self):
        task = self.create(target='simulation', project='last')
        task['projects'] = ['first','last']
        def status(project):
            return {'simulation':'current' if project.name == 'last' else 'stale'}
        with patch('fpga_agent.autonomy.evidence', side_effect=status):
            passed, reason = completion_gate(self.workspace, task)
        self.assertFalse(passed)
        self.assertIn('first',reason)

    async def test_repeated_tool_failure_replans_then_stops_without_false_success(self):
        task = self.create(max_failures=2, project="demo")
        engine = ScriptedEngine(self.store, task["id"], [decision()] * 3)
        result = await engine.run()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(engine.tool_calls, 2)
        self.assertEqual(result["steps"][1]["status"], "failed")
        request = read_json(self.store.directory(task["id"]) / "step-0002/request.json")
        self.assertEqual(request["context"]["history"][0]["exit_code"], 2)
        self.assertIn("synthetic failed tool", request["context"]["history"][0]["output_tail"][0])

    async def test_step_budget_survives_restart_and_requires_explicit_extension(self):
        task = self.create(max_steps=1)
        result = await ScriptedEngine(self.store, task["id"], [decision()]).run()
        self.assertEqual(result["status"], "limited")
        with self.assertRaisesRegex(ValueError, "预算"):
            self.store.launch(task["id"])
        self.assertEqual(self.store.get(task["id"])["iterations"], 1)

    async def test_long_history_is_archived_and_checkpoint_stays_bounded(self):
        task = self.create(max_steps=20)
        choices = [decision(brief=f'阶段 {i}') for i in range(20)]
        result = await ScriptedEngine(self.store, task['id'], choices).run()
        self.assertEqual(result['status'], 'limited')
        self.assertEqual(result['iterations'], 20)
        self.assertEqual(len(result['steps']), 16)
        directory = self.store.directory(task['id'])
        self.assertEqual(len(list(directory.glob('step-*/result.json'))), 20)
        self.assertEqual(read_json(directory/'step-0001/result.json')['iteration'],1)
        self.assertEqual(result['memory'], '保留验证要求')

    async def test_context_overflow_blocks_before_model_call_and_preserves_original_goal(self):
        task=self.store.create('要求'*10000,context_chars=16000)
        engine=ScriptedEngine(self.store,task['id'],[])
        result=await engine.run()
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['iterations'],0)
        self.assertEqual(result['objective'],'要求'*10000)
        self.assertIn('context-chars',result['reason'])

    async def test_read_tools_load_and_unload_skills_without_spawning_a_tool_process(self):
        (self.workspace.root/'core.v').write_text('module x; endmodule')
        choices=[decision('inspect',path='core.v'),decision('skill',query='bundled/rv32-validation'),
                 decision('unload-skill',query='bundled/rv32-validation'),decision('none','blocked')]
        task=self.create()
        engine=ScriptedEngine(self.store,task['id'],choices)
        result=await engine.run()
        self.assertEqual(engine.tool_calls,0)
        self.assertEqual(result['steps'][0]['observation']['path'],'core.v')
        self.assertEqual(result['loaded_skills'],[])
        directory=self.store.directory(task['id'])
        third=read_json(directory/'step-0003/request.json')['context']
        self.assertEqual(third['loaded_skills'][0]['id'],'bundled/rv32-validation')
        self.assertTrue((directory/'step-0001/output.log').exists())

    async def test_changed_skill_is_marked_stale_instead_of_reusing_an_old_body(self):
        from fpga_agent.knowledge import SkillCatalog
        path=self.workspace.root/'.agents/skills/example/SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nname: example\ndescription: test\n---\noriginal-procedure')
        task=self.create()
        task['loaded_skills']=[SkillCatalog(self.workspace).load('workspace/.agents/example')]
        self.store.save(task)
        path.write_text('---\nname: example\ndescription: test\n---\nchanged-procedure')
        context=TaskEngine(self.store,task['id']).context()
        self.assertTrue(context['loaded_skills'][0]['stale'])
        self.assertNotIn('content',context['loaded_skills'][0])

    async def test_log_limits_are_explicit_and_keep_the_latest_output_tail(self):
        task=self.create()
        engine=TaskEngine(self.store,task['id'])
        engine.task.update(iterations=1,steps=[{'iteration':1,'status':'running'}])
        with patch('fpga_agent.autonomy.TASK_LOG_BYTES',10),patch('fpga_agent.autonomy.STEP_LOG_BYTES',10):
            engine.output('first long line that fills the log')
            engine.output('last observed error')
            engine.output('final output')
        self.assertTrue(engine.task['steps'][0]['log_truncated'])
        self.assertIn('final output',engine.tail)
        log=(engine.directory/'step-0001/output.log').read_text(encoding='utf-8')
        self.assertEqual(log.count('ICFORGE_LOG_LIMIT'),1)

    async def test_provider_context_overflow_reduces_input_budget_with_bounded_retry(self):
        task=self.create(max_steps=3)
        class OverflowEngine(ScriptedEngine):
            async def command(inner,argv):
                if inner.task['iterations']==1:
                    inner.output('HTTP 400 context_length_exceeded: prompt too long')
                    return 1
                return await super(OverflowEngine,inner).command(argv)
        result=await OverflowEngine(self.store,task['id'],[decision('none','blocked')]).run()
        self.assertEqual(result['limits']['context_chars'],67200)
        self.assertEqual(result['steps'][0]['status'],'failed')
        self.assertEqual(result['iterations'],2)

    async def test_invalid_tool_never_executes_arbitrary_model_command(self):
        task = self.create(max_failures=1)
        engine = ScriptedEngine(self.store, task["id"], [decision("shell")])
        result = await engine.run()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(engine.tool_calls, 0)

    async def test_time_budget_kills_silent_command_and_preserves_interrupted_step(self):
        task = self.create(max_hours=0.0002)
        command = [sys.executable, "-c", "import time;time.sleep(30)"]
        engine = ScriptedEngine(self.store, task["id"], [decision()], real_tools=True)
        with patch("fpga_agent.autonomy.make_plan", return_value=TaskPlan("long", [command])):
            result = await asyncio.wait_for(engine.run(), 7)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["steps"][-1]["status"], "interrupted")
        self.assertNotIn("child_pid", result)

    async def test_pause_interrupts_then_resume_replans_and_reads_steering_once(self):
        task = self.create()
        command = [sys.executable, "-u", "-c", "import time;print('started',flush=True);time.sleep(30)"]
        engine = ScriptedEngine(self.store, task["id"], [decision()], real_tools=True)
        with patch("fpga_agent.autonomy.make_plan", return_value=TaskPlan("long", [command])):
            running = asyncio.create_task(engine.run())
            for _ in range(100):
                if engine.tail:
                    break
                await asyncio.sleep(0.03)
            child = self.store.get(task["id"])["child_pid"]
            self.assertTrue(pid_alive(child))
            self.store.control(task["id"], "pause")
            paused = await asyncio.wait_for(running, 7)
        self.assertEqual(paused["status"], "paused")
        self.assertFalse(pid_alive(child))
        self.assertEqual(paused["steps"][-1]["status"], "interrupted")
        self.store.steer(task["id"], "先检查时钟约束")
        directory = self.store.directory(task["id"])
        (directory / "control.json").unlink()
        resumed = await ScriptedEngine(self.store, task["id"], [decision("none", "blocked")]).run()
        self.assertEqual(resumed["iterations"], 2)
        request = read_json(directory / "step-0002/request.json")
        self.assertEqual(request["context"]["history"][0]["status"], "interrupted")
        self.assertEqual(request["context"]["steering"][0]["message"], "先检查时钟约束")
        self.assertEqual(list((directory / "inbox").glob("*.json")), [])

    async def test_dead_worker_cannot_overlap_a_still_running_tool(self):
        task = self.create()
        task.update(status="running", child_pid=os.getpid())
        self.store.save(task)
        self.assertEqual(self.store.get(task["id"])["status"], "interrupted")
        with self.assertRaisesRegex(ValueError, "仍运行"):
            self.store.launch(task["id"])
        with self.assertRaisesRegex(ValueError, "子进程"):
            self.store.control(task["id"], "cancel")

    async def test_task_logs_and_context_redact_inherited_credentials(self):
        secret = "test-secret-for-persistent-task"
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
            task = self.store.create("检查 " + secret)
            self.store.steer(task["id"], "附加 " + secret)
            await ScriptedEngine(self.store, task["id"], [decision("none", "blocked")]).run()
        for path in self.workspace.state.rglob("*.json*"):
            self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    async def test_delivery_gate_rejects_stale_packaged_testbench_even_when_live_gates_pass(self):
        project = self.workspace.root / "demo"
        project.mkdir()
        write_json(project / "project.json", {"project": "demo"})
        write_json(project / "reports/td.json", {"bitstream_hashes": {"design.bit": hashlib.sha256(b"bits").hexdigest()}})
        task = self.create(project="demo")
        artifact = project / "deliverables/demo-delivery.zip"
        artifact.parent.mkdir()
        inputs = {"@config": "physical-current"}
        verified = {"tb/test.v": hashlib.sha256(b"new-testbench").hexdigest()}

        def package(testbench):
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("delivery.json", json.dumps({"kind": "implemented", "inputs": inputs,
                    "bitstreams": {"design.bit": hashlib.sha256(b"bits").hexdigest()}}))
                archive.writestr("reports/simulation.json", json.dumps({"verification_inputs": verified}))
                archive.writestr("design.bit", b"bits")
                archive.writestr("tb/test.v", testbench)
            artifact.with_suffix(".zip.sha256").write_text(hashlib.sha256(artifact.read_bytes()).hexdigest())

        with patch("fpga_agent.autonomy.evidence", return_value={"simulation": "current", "implementation": "current"}), \
             patch("fpga_agent.evidence.input_hashes", return_value=inputs), \
             patch("fpga_agent.evidence.simulation_input_hashes", return_value=verified):
            package(b"old-testbench")
            self.assertFalse(completion_gate(self.workspace, task)[0])
            package(b"new-testbench")
            self.assertTrue(completion_gate(self.workspace, task)[0])


class HardwareCompletionTests(unittest.TestCase):
    def test_implemented_project_with_hardware_requirement_cannot_finish_without_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));project=workspace.root/'demo';project.mkdir()
            (project/'project.json').write_text(json.dumps({'hardware_acceptance':{'required':True,'profile':'hdmi-cube'}}))
            with patch('fpga_agent.autonomy.evidence',return_value={'simulation':'current','implementation':'current'}), \
                 patch('fpga_agent.video_hardware.evidence_valid',return_value=(False,'no rotating capture')):
                valid,reason=completion_gate(workspace,{'target':'delivery','project':'demo'})
            self.assertFalse(valid);self.assertIn('no rotating capture',reason)


class DetachedTaskIntegrationTests(unittest.TestCase):
    def test_detached_worker_replans_from_real_tool_evidence_and_completes(self):
        local = Path(__file__).resolve().parents[1] / ".tools/icarus/bin/iverilog.exe"
        if not shutil.which("iverilog") and not local.is_file():
            self.skipTest("Icarus required for real verification")
        calls = []
        choices = [decision(), decision('skill',query='bundled/hx1p35a-delivery'),
            decision('inspect',path='project.json'),decision('search',query='module',path='rtl'),
            decision('recall',query='demo'), decision("verify"), decision("none", "complete")]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"output_text": json.dumps(choices[min(len(calls)-1, len(choices)-1)])}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as temp:
            store = TaskStore(Workspace(Path(temp)))
            settings = Settings(provider="api", model="local-fixture", base_url=f"http://127.0.0.1:{server.server_port}/v1")
            store.workspace.save_settings(settings)
            secret = "offline-fixture-no-real-credential"
            env = dict(os.environ, OPENAI_API_KEY=secret, PYTHONIOENCODING="utf-8")
            parent = subprocess.run([sys.executable, '-m', 'fpga_agent', 'task', 'start',
                '创建 demo 并通过真实仿真验收', '--workspace', temp, '--target', 'simulation'],
                env=env, capture_output=True, text=True, encoding='utf-8', timeout=10)
            task = store.get()
            worker_log_path = store.directory(task["id"]) / "worker.log"
            worker_log = worker_log_path.read_text(encoding="utf-8", errors="replace") if worker_log_path.exists() else "<missing>"
            self.assertEqual(parent.returncode, 0,
                f"worker launcher exited {parent.returncode}\nstdout:\n{parent.stdout}\nstderr:\n{parent.stderr}\nworker.log:\n{worker_log}")
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    result = store.get(task["id"])
                    if result["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.05)
                worker_log_path = store.directory(task["id"]) / "worker.log"
                worker_log = worker_log_path.read_text(encoding="utf-8", errors="replace") if worker_log_path.exists() else "<missing>"
                owner = read_json(store.workspace.state / "task-worker.json", {})
                details = {"task": result, "worker_alive": pid_alive(result.get("pid")),
                    "lease_busy": store.busy(), "owner": owner, "worker_log": worker_log}
                self.assertEqual(result["status"], "completed", details)
                self.assertEqual(len(calls), 7)
                self.assertEqual(result["iterations"], 7)
                self.assertTrue(completion_gate(store.workspace, result)[0])
                self.assertIn('"exit_code": 0', json.dumps(calls[-1], ensure_ascii=False).replace('\\"', '"'))
                self.assertIn('bundled/hx1p35a-delivery',json.dumps(calls[-1],ensure_ascii=False))
                for path in store.workspace.state.rglob("*.json*"):
                    self.assertNotIn(secret, path.read_text(encoding="utf-8"))
            finally:
                if store.busy():
                    store.control(task["id"], "cancel")
                for _ in range(100):
                    if not store.busy() and not pid_alive(result.get("pid")):
                        break
                    time.sleep(0.03)


if __name__ == "__main__":
    unittest.main()
