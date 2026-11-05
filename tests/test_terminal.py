import asyncio
import json
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from fpga_agent.terminal import ProcessRunner, Session, Settings, Workspace, make_plan, redact, read_json, write_json


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def test_session_roundtrip_and_config_drops_credentials(self):
        session = Session(messages=[{"role": "user", "content": "设计一个 FIFO"}])
        self.workspace.save_session(session)
        self.assertEqual(self.workspace.resume().messages, session.messages)
        self.workspace.save_settings(Settings(model="local-model"))
        settings = self.workspace.settings()
        self.assertEqual(settings.model, "local-model")
        config = json.loads((self.workspace.state / "config.json").read_text())
        self.assertNotIn("api_key", config)

    def test_attachment_and_windows_style_quoted_path_are_context_not_code(self):
        path = self.workspace.root / "core notes.v"
        path.write_text("module demo; endmodule", encoding="utf-8")
        (self.workspace.root / "CLAUDE.md").write_text("Use synchronous resets.")
        context = self.workspace.context(None, '@"core notes.v"')
        self.assertIn("core notes.v", context["attachments"])
        self.assertIn("CLAUDE.md", context["project_instructions"])
        with self.assertRaises(ValueError):
            self.workspace.context(None, "@../secret.txt")
        with self.assertRaises(ValueError):
            self.workspace.context(None, "@.env")

    def test_tool_plan_preserves_prompt_as_one_argument_and_rejects_arbitrary_tool(self):
        brief = '计数器; $(whoami) "quoted"'
        plan = make_plan(self.workspace, Settings(), "design", {"project": "demo", "brief": brief, "full": True}, None)
        self.assertEqual(len(plan.commands), 3)
        self.assertEqual(plan.commands[1][plan.commands[1].index("--brief") + 1], brief)
        self.assertEqual(plan.commands[2][-2], "flow")
        with self.assertRaises(ValueError):
            make_plan(self.workspace, Settings(), "shell", {"project": "demo"}, None)
        with self.assertRaises(ValueError):
            make_plan(self.workspace, Settings(), "init", {"project": "../outside"}, None)
        with self.assertRaises(ValueError):
            make_plan(self.workspace, Settings(), "cpu-design", {"project": "demo", "architecture": "riscv;exec"}, None)

    def test_redaction(self):
        self.assertEqual(redact("key=test-password", {"OPENAI_API_KEY": "test-password"}), "key=[redacted]")

    def test_source_excerpts_expose_full_file_hash_and_truncation(self):
        project=self.workspace.root/'indexed'
        (project/'rtl').mkdir(parents=True)
        path=project/'rtl/core.v'
        path.write_text('wire x;\n'*3000+'// tail-only-marker',encoding='utf-8')
        write_json(project/'project.json',{'project':'indexed','rtl_sources':['rtl/core.v'],'testbench_sources':[]})
        context=self.workspace.context(project)
        index=context['source_index']['rtl/core.v']
        self.assertTrue(index['truncated'])
        self.assertEqual(index['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertNotIn('tail-only-marker',context['sources']['rtl/core.v'])

    def test_atomic_checkpoints_remain_readable_during_concurrent_updates(self):
        path = self.workspace.state/'checkpoint.json'
        write_json(path, {'iteration':0})
        errors=[]
        def update():
            try:
                for i in range(100):
                    write_json(path, {'iteration':i, 'payload':'x'*500})
            except Exception as exc:
                errors.append(exc)
        thread=threading.Thread(target=update)
        thread.start()
        while thread.is_alive():
            self.assertIsInstance(read_json(path), dict)
        thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(read_json(path)['iteration'],99)


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_json_event_is_preserved_above_normal_terminal_line_limit(self):
        import json
        lines=[];runner=ProcessRunner()
        code=await runner.run([sys.executable,'-c',"import json; print(json.dumps({'type':'event','payload':'x'*60000}))"],
                              Path.cwd(),dict(os.environ),lines.append,line_limit=1_000_000)
        self.assertEqual(code,0)
        self.assertEqual(len(lines),1)
        self.assertEqual(len(json.loads(lines[0])['payload']),60000)

    async def test_large_unicode_stdin_is_literal_and_does_not_deadlock_stdout(self):
        runner=ProcessRunner()
        payload=('你好 $HOME $(whoami) `echo unsafe` ; & |\n' * 10000)
        lines=[]
        script="import sys,hashlib; print('x'*100000,flush=True); print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"
        code=await asyncio.wait_for(runner.run([sys.executable,'-c',script],Path.cwd(),
            dict(os.environ,PYTHONIOENCODING='utf-8'),lines.append,stdin_text=payload),10)
        import hashlib
        self.assertEqual(code,0)
        self.assertEqual(lines[-1],hashlib.sha256(payload.encode('utf-8')).hexdigest())

    async def test_streams_unicode_and_long_lines_without_pipe_overflow(self):
        runner = ProcessRunner()
        lines = []
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        code = await runner.run([sys.executable, "-c", "print('计数器' * 40000);print('done')"], Path.cwd(), env, lines.append)
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "done")
        self.assertNotIn("\ufffd", "".join(lines))
        self.assertGreater(len("".join(lines)), 100000)

    async def test_cancel_terminates_parent_and_child(self):
        runner = ProcessRunner()
        lines = []
        script = "import subprocess,sys,time;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);print(p.pid,flush=True);time.sleep(60)"
        task = asyncio.create_task(runner.run([sys.executable, "-u", "-c", script], Path.cwd(), dict(os.environ), lines.append))
        for _ in range(100):
            if lines:
                break
            await asyncio.sleep(0.03)
        self.assertTrue(lines)
        child_pid = int(lines[0])
        await runner.cancel()
        await asyncio.wait_for(task, 6)
        self.assertTrue(runner.cancelled)
        if os.name == "nt":
            result = subprocess.run(["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"], capture_output=True)
            self.assertNotIn(f'"{child_pid}"'.encode(), result.stdout)
        else:
            # A terminated child may briefly remain a zombie until adopted/reaped.
            status = Path(f"/proc/{child_pid}/stat")
            if status.is_file():
                self.assertEqual(status.read_text().split()[2], "Z")
