import asyncio
import json
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from textual.widgets import Input

from fpga_agent.project import create_project
from fpga_agent.terminal import TaskPlan, read_json
from fpga_agent.tui import Connection, ForgeApp, Picker, Prompt


class TerminalUITests(unittest.IsolatedAsyncioTestCase):
    async def test_orchestration_commands_persist_settings_and_obey_plan_mode(self):
        app=ForgeApp(self.root)
        app.settings.provider='codex'
        app.settings.model='native'
        app.settings.mode='plan'
        async with app.run_test(size=(110,40)):
            await app.submit_command('/mode orchestrate')
            await app.submit_command('/agents 2 codex')
            await app.submit_command('/agent-model opencode opencode/deepseek-v4.1-flash-free')
            self.assertEqual(app.workspace.settings().agent_opencode_model,'opencode/deepseek-v4.1-flash-free')
            self.assertEqual(app.workspace.settings().execution,'orchestrate')
            self.assertEqual(app.workspace.settings().agent_workers,'2')
            await app.submit_command('并行实现两个模块并审查')
            argv=app.pending.commands[0]
            self.assertEqual(argv[argv.index('--mode')+1],'orchestrate')
            self.assertEqual(argv[argv.index('--target')+1],'workspace')
            self.assertEqual(argv[argv.index('--max-agents')+1],'2')
            self.assertIn('opencode=opencode/deepseek-v4.1-flash-free',argv)
            self.assertFalse(list((app.workspace.state/'tasks').glob('*/task.json')))
            await app.submit_command('/mode engineer')
            await app.submit_command('/orchestrate --workspace 修复测试')
            self.assertIn('修复测试',app.pending.commands[0])
            self.assertEqual(app.pending.commands[0][app.pending.commands[0].index('--mode')+1],'orchestrate')
            await app.submit_command('/agent-model opencode native')
            self.assertEqual(app.workspace.settings().agent_opencode_model,'')

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    async def wait_idle(self, app, pilot):
        for _ in range(300):
            await pilot.pause(0.04)
            if not app.busy and not app.queued_input and not app.processing_queue:
                return
        self.fail("UI task did not finish")

    async def test_long_task_obeys_plan_mode_and_autonomy_setting_persists(self):
        app = ForgeApp(self.root)
        app.extra_env["OPENAI_API_KEY"] = "fixture-not-real"
        app.settings.mode = "plan"
        async with app.run_test(size=(110, 40)):
            await app.submit_command('/task --simulation-only 创建 FIFO 并验证')
            argv = app.pending.commands[0]
            self.assertEqual(argv[4:6], ['task', 'start'])
            self.assertIn('创建 FIFO 并验证', argv)
            self.assertEqual(argv[argv.index('--target')+1], 'simulation')
            self.assertFalse(list((app.workspace.state/'tasks').glob('*/task.json')))
            await app.submit_command('/auto off')
            self.assertEqual(app.workspace.settings().autonomy, 'manual')

    async def test_skills_recall_and_context_commands_render_without_starting_work(self):
        from fpga_agent.autonomy import TaskStore
        app=ForgeApp(self.root)
        async with app.run_test(size=(120,42)):
            store=TaskStore(app.workspace)
            task=store.create('RV32I benchmark memory')
            task.update(status='paused',context_budget={'method':'JSON characters','after_chars':1234})
            store.save(task)
            await app.submit_command('/skills')
            await app.submit_command('/skill bundled/rv32-validation')
            await app.submit_command('/recall RV32I')
            await app.submit_command('/context')
            text='\n'.join(m['content'] for m in app.session.messages)
            self.assertIn('RV32I independent verification',text)
            self.assertIn('historical_only',text)
            self.assertIn('1234',text)
            self.assertFalse(app.busy)

    async def test_background_task_accepts_steering_and_blocks_overlapping_tools(self):
        from fpga_agent.autonomy import Lease, TaskStore
        from fpga_agent.terminal import write_json
        app = ForgeApp(self.root)
        async with app.run_test(size=(110, 40)):
            store = TaskStore(app.workspace)
            task = store.create('完成 FIFO')
            write_json(app.workspace.state/'task-worker.json', {'id':task['id']})
            lease = Lease(store.lock)
            try:
                await app.submit_command('增加复位边界覆盖')
                self.assertEqual(len(list((store.directory(task['id'])/'inbox').glob('*.json'))), 1)
                with self.assertRaisesRegex(ValueError, '长期任务'):
                    await app.submit_command('/verify')
                await app.submit_command('/tasks')
                self.assertTrue(any(task['id'] in m['content'] for m in app.session.messages))
            finally:
                lease.close()

    async def test_complex_model_action_promotes_to_durable_task_by_default(self):
        from fpga_agent.terminal import write_json
        app = ForgeApp(self.root)
        async with app.run_test(size=(110, 40)):
            app.begin_job('model fixture')
            directory = app.job_directory

            class Runner:
                cancelled = False
                async def run(self, *args):
                    write_json(directory/'response.json', {'message':'设计 FIFO', 'action':'design',
                        'arguments':{'project':'fifo','brief':'FIFO depth=16 width=32，保留同步复位要求','full':True}})
                    return 0

            app.runner = Runner()
            with patch.object(app, 'start_plan') as start:
                await app.chat({'mode':'build','messages':[{'role':'user','content':'设计 FIFO 并完整交付'}]})
                argv = start.call_args[0][0].commands[0]
                self.assertEqual(argv[4:6], ['task','start'])
                self.assertIn('设计 FIFO 并完整交付', argv[6])
                self.assertIn('depth=16 width=32',argv[6])
                self.assertEqual(argv[argv.index('--target')+1], 'delivery')

    async def test_failed_autonomous_handoff_never_falls_back_to_executing_the_old_plan(self):
        from fpga_agent.terminal import write_json
        app=ForgeApp(self.root)
        async with app.run_test(size=(110,40)):
            app.begin_job('oversized handoff fixture')
            directory=app.job_directory
            class Runner:
                cancelled=False
                async def run(self,*args):
                    write_json(directory/'response.json',{'message':'candidate','action':'design',
                        'arguments':{'project':'fifo','brief':'b'*60000,'full':True}})
                    return 0
            app.runner=Runner()
            with patch.object(app,'start_plan') as start:
                await app.chat({'mode':'build','messages':[{'role':'user','content':'按讨论的规格继续设计并交付'}]})
                start.assert_not_called()
            self.assertFalse((self.root/'fifo').exists())
            self.assertTrue(any('60,000' in m['content'] for m in app.session.messages))

    async def test_keyboard_palette_history_multiline_and_responsive_layout(self):
        app = ForgeApp(self.root)
        async with app.run_test(size=(140, 46)) as pilot:
            prompt = app.query_one(Prompt)
            prompt.load_text("/hel")
            await pilot.press("tab")
            self.assertEqual(prompt.text, "/help ")
            await pilot.press("enter")
            await pilot.pause()
            self.assertTrue(any("在终端完成设计闭环" in m["content"] for m in app.session.messages))
            await pilot.press("up")
            self.assertEqual(prompt.text, "/help")
            prompt.load_text("line")
            prompt.move_cursor(prompt.document.end)
            await pilot.press("shift+enter")
            self.assertIn("\n", prompt.text)
            await pilot.press("ctrl+p")
            self.assertIsInstance(app.screen, Picker)
            await pilot.press("escape", "shift+tab")
            self.assertEqual(app.settings.mode, "plan")
            await pilot.resize_terminal(78, 28)
            self.assertEqual(len(app.query('#inspector')),0)
            self.assertEqual(len(app.query('#sidebar')),0)
            self.assertGreater(app.query_one(Prompt).size.width, 25)

    async def test_busy_prompts_execute_fifo_without_duplicate_history(self):
        import sys
        app = ForgeApp(self.root)
        async with app.run_test(size=(100,36)) as pilot:
            app.start_plan(TaskPlan('queued-input integration',[[sys.executable,'-c','import time; time.sleep(1.5)']]))
            for name in ('first','second'):
                app.query_one(Prompt).load_text('/init '+name)
                await pilot.press('enter')
            self.assertTrue(app.queued_input)
            await self.wait_idle(app,pilot)
            for name in ('first','second'):
                self.assertTrue((self.root/name/'project.json').is_file())
                self.assertEqual(sum(m['content']=='/init '+name for m in app.session.messages),1)
            self.assertEqual(app.selected.name,'second')
            self.assertFalse(any(m.get('queue_id') for m in app.session.messages))

    async def test_startup_character_banner_survives_resize_and_stays_in_scrollback(self):
        app=ForgeApp(self.root)
        async with app.run_test(size=(156,46)) as pilot:
            for width,height in ((80,24),(58,28),(156,46)):
                await pilot.resize_terminal(width,height)
                await pilot.pause()
                banner=app.query_one('#wordmark')
                self.assertTrue(banner.display)
                self.assertGreaterEqual(banner.region.height,5)
                self.assertLessEqual(app.query_one(Prompt).region.bottom,height)
            await app.add_message('user','Hello')
            self.assertTrue(app.query_one('#welcome').display)
            await app.new_session()
            self.assertTrue(app.query_one('#wordmark').display)

    async def test_review_tool_renders_gate_findings_when_artifacts_are_blocked(self):
        project=create_project('review',self.root/'review')
        app=ForgeApp(self.root)
        async with app.run_test(size=(120,42)) as pilot:
            await app.select_project(project)
            await app.submit_command('/review --simulation-only')
            await self.wait_idle(app,pilot)
            message=next(m['content'] for m in app.session.messages if m['role']=='tool')
            self.assertIn('DV-001',message)
            self.assertIn('存在待关闭项',message)

    async def test_connection_key_is_not_written_and_resume_restores_conversation(self):
        app = ForgeApp(self.root)
        async with app.run_test(size=(140, 46)) as pilot:
            await app.submit_command("/connect")
            await pilot.pause()
            self.assertIsInstance(app.screen, Connection)
            app.screen.query_one("#api-key", Input).value = "test-credential-do-not-persist"
            app.screen.query_one("#model", Input).value = "test-model"
            await pilot.click("#connection-save")
            await pilot.pause()
            self.assertEqual(app.settings.model, "test-model")
            await app.add_message("user", "a retained message")
            old_id = app.session.id
            await app.new_session()
            await app.load_session(old_id)
            self.assertIn("a retained message", app.session.messages[-1]["content"])
            for path in (self.root / ".fpga-agent").rglob("*.json"):
                self.assertNotIn("test-credential-do-not-persist", path.read_text(encoding="utf-8"))

    async def test_plan_mode_defers_actual_creation_until_run(self):
        app = ForgeApp(self.root)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.submit_command("/plan")
            await app.submit_command("/init demo")
            self.assertFalse((self.root / "demo/project.json").exists())
            self.assertIsNotNone(app.pending)
            await app.submit_command("/run")
            await self.wait_idle(app, pilot)
            self.assertTrue((self.root / "demo/project.json").is_file())
            self.assertEqual(app.selected.name, "demo")

    async def test_real_model_transport_to_ui_tool_to_icarus(self):
        local_iverilog = Path(__file__).resolve().parents[1] / ".tools/icarus/bin/iverilog.exe"
        if not shutil.which("iverilog") and not local_iverilog.is_file():
            self.skipTest("Icarus is required for the integration test")
        captured = []
        payload = {"message": "运行独立仿真。", "action": "verify", "arguments": {"project": "", "brief": "", "architecture": "multi", "full": False, "simulation_only": False}}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"output_text": json.dumps(payload, ensure_ascii=False)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        project = create_project("demo", self.root / "demo")
        app = ForgeApp(self.root)
        app.settings.base_url = f"http://127.0.0.1:{server.server_port}/v1"
        app.extra_env["OPENAI_API_KEY"] = "integration-test-not-a-real-key"
        async with app.run_test(size=(140, 46)) as pilot:
            await app.select_project(project)
            app.query_one(Prompt).load_text("请检查当前工程")
            await pilot.press("enter")
            await self.wait_idle(app, pilot)
            report = read_json(project / "reports/simulation.json", {})
            self.assertEqual(report.get("status"), "pass", app.tail)
            self.assertTrue(captured)
            self.assertTrue(any(m["role"] == "tool" and "通过" in m["content"] for m in app.session.messages))
            for path in app.workspace.state.rglob("*.json"):
                self.assertNotIn("integration-test-not-a-real-key", path.read_text(encoding="utf-8"))

    async def test_failed_tool_is_visible_and_does_not_report_pass(self):
        app = ForgeApp(self.root)
        async with app.run_test(size=(120, 40)) as pilot:
            import sys
            app.start_plan(TaskPlan("intentional failure", [[sys.executable, "-c", "print('tool rejected input');raise SystemExit(9)"]]))
            await self.wait_idle(app, pilot)
            self.assertTrue(any("exit 9" in m["content"] for m in app.session.messages))
            self.assertFalse(app.busy)

    async def test_attachment_picker_and_new_session_input_history(self):
        project = create_project("demo", self.root / "demo")
        app = ForgeApp(self.root)
        async with app.run_test(size=(120, 40)) as pilot:
            await app.select_project(project)
            prompt = app.query_one(Prompt)
            prompt.load_text("检查 @rtl/")
            await pilot.press("tab")
            self.assertIsInstance(app.screen, Picker)
            app.screen.query_one(Input).value = "rtl/"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIn("@rtl/hx1p35a_heartbeat.v", prompt.text)
            prompt.remember("/help")
            await app.new_session()
            self.assertEqual(prompt.input_history, [])

    async def test_natural_local_action_respects_plan_mode(self):
        project = create_project("demo", self.root / "demo")
        app = ForgeApp(self.root)
        async with app.run_test(size=(120, 40)):
            await app.select_project(project)
            app.settings.mode = "plan"
            await app.submit_command("运行仿真")
            self.assertIsNotNone(app.pending)
            self.assertFalse(app.busy)
            self.assertFalse((project / "reports/simulation.json").exists())
