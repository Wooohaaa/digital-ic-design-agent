import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.agent_backends import Events, build_command, environment, command_prefix
from fpga_agent.autonomy import TaskEngine, TaskStore, completion_gate
from fpga_agent.orchestration import Orchestrator, git, validate_jobs, verify_workspace, workspace_gate
from fpga_agent.terminal import Workspace, read_json, write_json

CATALOG=[{'name':name,'available':True} for name in ('codex','claude','opencode')]


def job(name, *, files=None, deps=None, role='implement', prompt='value = 1', executor='codex'):
    return {'id':name,'executor':executor,'role':role,'complexity':'medium','prompt':prompt,
            'files':files if files is not None else [name+'.py'],'depends_on':deps or []}


STUB = r'''
import json,sys,time
from pathlib import Path
data=json.loads(sys.stdin.read().splitlines()[-1]); j=data['job']
print(json.dumps({'type':'thread.started','thread_id':'fixture-'+j['id']}),flush=True)
print(json.dumps({'type':'fixture.start','time':time.time()}),flush=True)
if j['prompt']=='WAIT' and not sys.argv[1]: time.sleep(30)
elif j['prompt']=='SLOW': time.sleep(8)
else: time.sleep(0.3)
if j['role']=='review':
    assert Path('a.py').exists() and Path('b.py').exists(), 'review must see dependencies'
elif j['role']=='implement':
    p=Path('outside.py') if j['prompt']=='OUTSIDE' else Path(j['files'][0])
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text('value = 1' if j['prompt'] in ('WAIT','LOGICAL_ERROR','OUTSIDE','SLOW') else j['prompt'])
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Finished scoped job '+j['id']}}),flush=True)
if j['prompt']=='LOGICAL_ERROR': print(json.dumps({'type':'error','message':'logical failure'}),flush=True)
print(json.dumps({'type':'fixture.end','time':time.time()}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}),flush=True)
if j['prompt']=='PATCH_LOOP' and not sys.argv[1]:
    for i in range(3):print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'apply_patch','exit_code':1,'aggregated_output':'requires a UTF-8 PATCH argument'}}),flush=True)
    time.sleep(30)
'''


def stub_command(name,directory,record,*,role,model='',session_id='',prefix=None):
    return [sys.executable,'-u','-c',STUB,session_id],{}


class TrackingEngine(TaskEngine):
    max_children=0
    def save(self):
        self.max_children=max(self.max_children,len(self.task.get('child_pids',{})))
        super().save()


class ScriptedOrchestrationEngine(TrackingEngine):
    async def command(self,argv):
        if 'fpga_agent.task_planner' in argv:
            write_json(Path(argv[-1]).with_name('decision.json'),next(self.decisions))
            return 0
        return await super().command(argv)


class PlanAndAdapterTests(unittest.TestCase):
    def test_repeated_patch_launcher_errors_are_classified_and_success_clears_them(self):
        events=Events('codex')
        for _ in range(3):
            events.accept(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'apply_patch',
                'exit_code':1,'aggregated_output':"Invalid patch: The last line of the patch must be '*** End Patch'"}}))
        self.assertEqual(events.launcher_failures,3)
        events.accept(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'[IO.File]::WriteAllText',
            'exit_code':0,'aggregated_output':''}}))
        self.assertEqual(events.launcher_failures,0)

    @unittest.skipUnless(os.name=='nt','Windows npm shims')
    def test_claude_npm_native_binary_and_legacy_js_are_detected_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); shim=root/'claude.cmd'; shim.write_text('fixture')
            package=root/'node_modules/@anthropic-ai/claude-code'; (package/'bin').mkdir(parents=True)
            native=package/'bin/claude.exe'; native.write_bytes(b'MZfixture')
            with patch('fpga_agent.agent_backends.shutil.which',side_effect=lambda name: str(shim) if name=='claude' else sys.executable):
                self.assertEqual(command_prefix('claude'),[str(native)])
                native.unlink(); (package/'cli.js').write_text('fixture')
                self.assertEqual(command_prefix('claude'),[sys.executable,str(package/'cli.js')])

    def test_dag_validation_cycles_unavailable_agents_and_scope_collisions(self):
        jobs=validate_jobs([job('a',files=['rtl/']),job('b',files=['rtl/core.v']),
                           job('review',files=[],role='review',deps=['a','b'])],['codex'],CATALOG)
        self.assertIn('a',jobs[1]['depends_on'])
        with self.assertRaisesRegex(ValueError,'环'):
            validate_jobs([job('a',deps=['b']),job('b',deps=['a'])],['codex'],CATALOG)
        with self.assertRaises(ValueError):
            validate_jobs([job('a',files=['../outside'])],['codex'],CATALOG)
        with self.assertRaises(ValueError):
            validate_jobs([job('a',executor='claude')],['codex'],CATALOG)
        with self.assertRaisesRegex(ValueError,'审查'):
            validate_jobs([job('a'),job('review',role='review',files=[])],['codex'],CATALOG)

    def test_native_commands_use_noninteractive_interfaces_without_bypass_flags(self):
        for backend in ('codex','claude','opencode'):
            argv,env=build_command(backend,Path('workspace'),Path('records'),role='implement',
                session_id='session-1',prefix=[backend])
            self.assertFalse(any('dangerously' in arg for arg in argv))
            self.assertIn('session-1',argv)
            if backend=='codex':
                self.assertIn('workspace-write',argv)
                self.assertIn('never',argv)
            elif backend=='claude':
                self.assertNotIn('Bash',argv[argv.index('--tools')+1])
            else:
                self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT'])['permission']['bash'],'deny')

    def test_event_parsers_capture_sessions_reports_usage_and_logical_errors(self):
        codex=Events('codex')
        codex.accept(json.dumps({'type':'thread.started','thread_id':'abc-1'}))
        codex.accept(json.dumps({'type':'turn.completed','usage':{'input_tokens':4,'output_tokens':7}}))
        self.assertEqual(codex.session_id,'abc-1')
        self.assertEqual(codex.usage['output_tokens'],7)
        claude=Events('claude')
        claude.accept(json.dumps({'type':'result','session_id':'abc','is_error':True,'result':'rejected'}))
        self.assertTrue(claude.errors)
        opencode=Events('opencode')
        opencode.accept(json.dumps({'type':'text','sessionID':'ses_1','part':{'text':'done'}}))
        self.assertEqual(opencode.text,['done'])
        self.assertEqual(opencode.session_id,'ses_1')

    def test_executor_auth_does_not_inherit_supervisor_gateway(self):
        env=environment({'OPENAI_API_KEY':'supervisor-key','OPENAI_BASE_URL':'https://supervisor',
                         'ICFORGE_EXECUTOR_OPENAI_API_KEY':'worker-key','ANTHROPIC_API_KEY':'native-claude'})
        self.assertEqual(env['OPENAI_API_KEY'],'worker-key')
        self.assertNotIn('OPENAI_BASE_URL',env)
        self.assertEqual(env['ANTHROPIC_API_KEY'],'native-claude')


class WorktreeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='icforge-orch-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        git(self.root,'init')
        (self.root/'.gitignore').write_text('.fpga-agent/\n__pycache__/\n')
        (self.root/'core.py').write_text('original = True\n')
        git(self.root,'add','-A'); git(self.root,'commit','-m','fixture')
        self.store=TaskStore(Workspace(self.root))
        task=self.store.create('完成两个模块和独立审查',mode='orchestrate',target='workspace',
            checks=[{'name':'host-check','argv':['python','-c',"from pathlib import Path;assert Path('a.py').is_file()"]}],
            max_agents=2,agent_timeout=15)
        self.engine=TrackingEngine(self.store,task['id'])
        self.catalog_patch=patch('fpga_agent.orchestration.available',return_value=CATALOG)
        self.command_patch=patch('fpga_agent.orchestration.build_command',side_effect=stub_command)
        self.catalog_patch.start(); self.command_patch.start()
        self.addCleanup(self.catalog_patch.stop); self.addCleanup(self.command_patch.stop)

    async def test_parallel_workers_then_dependent_review_and_host_acceptance(self):
        self.store.steer(self.engine.task['id'],'Preserve the fixed operator acceptance contract.')
        (self.root/'core.py').write_text('user_change = True\n')
        private=self.root/'llm/private.txt'
        private.parent.mkdir(); private.write_text('user unrelated data')
        result=await Orchestrator(self.engine).run([job('a'),job('b'),job('review',role='review',files=[],deps=['a','b'])])
        self.assertEqual(result['status'],'integrated',result)
        self.assertEqual(self.engine.max_children,2)
        self.assertEqual((self.root/'core.py').read_text(),'user_change = True\n')
        self.assertEqual(private.read_text(),'user unrelated data')
        self.assertEqual(git(self.root,'diff','--cached','--name-only').strip(),b'')
        batch=read_json(self.engine.directory/'delegations'/result['batch_id']/'batch.json')
        self.assertFalse((Path(batch['integration'])/'llm').exists())
        self.assertTrue(all(row['session_id'] for row in result['jobs']))
        self.assertEqual(len(self.engine.task['steering']),1)
        for name in ('a','b','review'):
            assignment=self.engine.directory/'delegations'/result['batch_id']/'jobs'/name/'attempt-1/assignment.txt'
            self.assertIn('Preserve the fixed operator acceptance contract.',assignment.read_text(encoding='utf-8'))
        checked=await verify_workspace(self.engine)
        self.assertEqual(checked['status'],'pass')
        self.assertTrue(completion_gate(self.store.workspace,self.engine.task)[0])
        (self.root/'a.py').write_text('different')
        self.assertFalse(workspace_gate(self.root,self.engine.task)[0])

    async def test_overlapping_implementations_are_serialized(self):
        result=await Orchestrator(self.engine).run([job('first',files=['a.py'],prompt='value = 1'),
            job('second',files=['a.py'],prompt='value = 2')])
        self.assertEqual(result['status'],'integrated',result)
        self.assertEqual(self.engine.max_children,1)
        self.assertEqual((self.root/'a.py').read_text(),'value = 2')

    async def test_integration_job_name_cannot_alias_shared_integration_worktree(self):
        result=await Orchestrator(self.engine).run([job('integration',files=['a.py'])])
        self.assertEqual(result['status'],'integrated',result)
        batch=read_json(self.engine.directory/'delegations'/result['batch_id']/'batch.json')
        self.assertNotEqual(batch['jobs'][0]['worktree'],batch['integration'])
        self.assertEqual(Path(batch['jobs'][0]['worktree']).parent.name,'workers')

    async def test_ready_dependency_uses_free_slot_before_slow_independent_peer_finishes(self):
        result=await Orchestrator(self.engine).run([job('a'),job('b',prompt='SLOW'),job('c',deps=['a'])])
        self.assertEqual(result['status'],'integrated',result)
        events={}
        for name in ('b','c'):
            path=self.engine.directory/'delegations'/result['batch_id']/'jobs'/name/'attempt-1/events.jsonl'
            events[name]=[json.loads(line) for line in path.read_text().splitlines()]
        c_start=next(e['time'] for e in events['c'] if e['type']=='fixture.start')
        b_end=next(e['time'] for e in events['b'] if e['type']=='fixture.end')
        self.assertLess(c_start,b_end)
        self.assertEqual(result['scheduling']['max_parallel_observed'],2)
        self.assertEqual(result['scheduling']['dispatch_order'],['a','b','c'])

    async def test_out_of_scope_patch_and_zero_exit_logical_error_are_not_imported(self):
        result=await Orchestrator(self.engine).run([job('a',prompt='OUTSIDE')])
        self.assertEqual(result['status'],'failed')
        self.assertFalse((self.root/'outside.py').exists())
        self.assertIn('范围之外',result['jobs'][0]['error'])
        self.assertFalse(completion_gate(self.store.workspace,self.engine.task)[0])

    async def test_native_logical_error_rejects_success_exit(self):
        result=await Orchestrator(self.engine).run([job('a',prompt='LOGICAL_ERROR')])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['jobs'][0]['status'],'failed')
        self.assertFalse((self.root/'a.py').exists())

    async def test_switching_failed_executor_drops_previous_native_session(self):
        self.engine.task['orchestration']['executors']=['codex','claude']
        result=await Orchestrator(self.engine).run([job('a'),job('b',deps=['a'],prompt='LOGICAL_ERROR')])
        batch=Orchestrator(self.engine).prepare([job('b',executor='claude',deps=['a'])],result['batch_id'])
        self.assertEqual(batch['jobs'][0]['status'],'completed')
        self.assertEqual(batch['jobs'][1]['executor'],'claude')
        self.assertEqual(batch['jobs'][1]['depends_on'],['a'])
        self.assertNotIn('session_id',batch['jobs'][1])

    async def test_pause_resume_keeps_completed_nodes_and_native_session(self):
        running=asyncio.create_task(Orchestrator(self.engine).run([job('a'),job('b',deps=['a'],prompt='WAIT')]))
        for _ in range(300):
            if 'b' in self.engine.task.get('child_pids',{}):
                await asyncio.sleep(0.2)
                break
            await asyncio.sleep(0.02)
        write_json(self.engine.directory/'control.json',{'action':'pause'})
        paused=await asyncio.wait_for(running,8)
        self.assertEqual(paused['status'],'interrupted',paused)
        self.assertFalse((self.root/'a.py').exists())
        (self.engine.directory/'control.json').unlink()
        restarted=ScriptedOrchestrationEngine(self.store,self.engine.task['id'])
        verify={'decision':'execute','message':'验收恢复结果','plan':['验收'],'memory':'恢复前置节点','action':'workspace-verify','arguments':{}}
        complete={'decision':'complete','message':'固定验收已通过','plan':[],'memory':'恢复完成','action':'none','arguments':{}}
        # Coverage instrumentation and cold Windows Python startup add latency;
        # retain bounded replanning turns instead of coupling this test to exactly
        # two model calls when the first host check needs another observation.
        restarted.decisions=iter([verify,complete] * 3)
        finished=await restarted.run()
        self.assertEqual(finished['status'],'completed',finished['reason'])
        resumed=finished['delegations'][-1]
        self.assertEqual(resumed['status'],'integrated',resumed)
        self.assertEqual(resumed['jobs'][0]['status'],'completed')
        batch=read_json(self.engine.directory/'delegations'/paused['batch_id']/'batch.json')
        self.assertEqual(batch['jobs'][0]['attempts'],1)
        self.assertEqual(batch['jobs'][1]['attempts'],2)
        self.assertIsNone(batch['jobs'][1]['error'])
        self.assertEqual(restarted.task['child_pids'],{})

    async def test_manual_change_blocks_import_and_retry_is_idempotent(self):
        running=asyncio.create_task(Orchestrator(self.engine).run([job('a')]))
        for _ in range(100):
            if self.engine.task.get('child_pids'):
                break
            await asyncio.sleep(0.02)
        (self.root/'a.py').write_text('manual change')
        failed=await running
        self.assertEqual(failed['status'],'failed',failed)
        self.assertEqual((self.root/'a.py').read_text(),'manual change')
        (self.root/'a.py').unlink()
        success=await Orchestrator(self.engine).run([],failed['batch_id'])
        self.assertEqual(success['status'],'integrated',success)
        batch=read_json(self.engine.directory/'delegations'/success['batch_id']/'batch.json')
        batch['status']='applying'
        write_json(self.engine.directory/'delegations'/success['batch_id']/'batch.json',batch)
        recovered=await Orchestrator(self.engine).run([],success['batch_id'])
        self.assertEqual(recovered['status'],'integrated')
        self.assertEqual((self.root/'a.py').read_text(),'value = 1')

    async def test_silent_worker_timeout_is_not_a_completed_job(self):
        self.engine.task['orchestration']['job_timeout_seconds']=1
        result=await Orchestrator(self.engine).run([job('a',prompt='WAIT')])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['jobs'][0]['status'],'failed')
        self.assertEqual(self.engine.task['child_pids'],{})

    async def test_known_launcher_loop_recovers_with_same_native_session_and_scope(self):
        self.engine.task['orchestration']['job_timeout_seconds']=10
        result=await Orchestrator(self.engine).run([job('a',prompt='PATCH_LOOP')])
        self.assertEqual(result['status'],'integrated',result)
        batch=read_json(self.engine.directory/'delegations'/result['batch_id']/'batch.json')
        self.assertEqual(batch['jobs'][0]['attempts'],2)
        self.assertEqual(batch['jobs'][0]['launcher_recoveries'],1)
        self.assertEqual(batch['jobs'][0]['failure_count'],0)

    async def test_multiple_operator_pauses_do_not_exhaust_failure_budget(self):
        orchestrator=Orchestrator(self.engine)
        batch=orchestrator.prepare([job('a')],'')
        batch['jobs'][0].update(status='interrupted',attempts=4)
        orchestrator.save(batch)
        result=await orchestrator.run([],batch['id'])
        self.assertEqual(result['status'],'integrated',result)
        stored=read_json(self.engine.directory/'delegations'/batch['id']/'batch.json')
        self.assertEqual(stored['jobs'][0]['failure_count'],0)
        self.assertEqual(stored['jobs'][0]['attempts'],5)

    async def test_new_untracked_directory_is_included_in_gate_and_next_batch(self):
        result=await Orchestrator(self.engine).run([job('new',files=['newpkg/module.py'])])
        self.assertEqual(result['status'],'integrated',result)
        self.assertEqual((await verify_workspace(self.engine))['status'],'fail')  # fixed contract requires a.py
        self.engine.task['checks'][0]['argv']=[sys.executable,'-c',"from pathlib import Path; assert Path('newpkg/module.py').read_text() == 'value = 1'"]
        self.assertEqual((await verify_workspace(self.engine))['status'],'pass')
        (self.root/'newpkg/module.py').write_text('manual change')
        self.assertFalse(workspace_gate(self.root,self.engine.task)[0])
        (self.root/'newpkg/module.py').write_text('value = 1')
        followup=await Orchestrator(self.engine).run([job('second',files=['a.py'])])
        batch=read_json(self.engine.directory/'delegations'/followup['batch_id']/'batch.json')
        self.assertEqual((Path(batch['integration'])/'newpkg/module.py').read_text(),'value = 1')

    async def test_persistent_engine_delegates_verifies_and_completes(self):
        engine=ScriptedOrchestrationEngine(self.store,self.engine.task['id'])
        self.assertIn('core.py',engine.context()['workspace_files']['paths'])
        def decision(action,kind='execute',**arguments):
            return {'decision':kind,'message':'检查宿主证据','plan':['实现','验收'],
                    'memory':'保留固定验收契约','action':action,'arguments':arguments}
        engine.decisions=iter([decision('delegate',jobs=[job('a'),job('b'),job('review',role='review',files=[],deps=['a','b'])]),
                               decision('workspace-verify'),decision('none','complete')])
        result=await engine.run()
        self.assertEqual(result['status'],'completed',result['reason'])
        self.assertEqual(engine.max_children,2)
        self.assertEqual([s['decision']['action'] for s in result['steps']],['delegate','workspace-verify','none'])
        self.assertTrue(completion_gate(self.store.workspace,result)[0])

    async def test_nested_workspace_is_rejected_before_launch(self):
        nested=self.root/'nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError,'根目录'):
            TaskStore(Workspace(nested)).create('work',mode='orchestrate')


if __name__=='__main__':
    unittest.main()
