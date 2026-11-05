"""Dependency-aware CLI delegation in isolated worktrees with guarded integration."""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

from .agent_backends import BACKENDS, Events, available, build_command, environment
from .claude_gateway import ClaudeGateway
from .terminal import ProcessRunner, read_json, redact, timestamp, write_json

ASSIGNMENT = '.icforge-assignment.md'


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    env = dict(os.environ,GIT_TERMINAL_PROMPT='0')
    hooks = root/'.fpga-agent/disabled-hooks'
    result = subprocess.run(['git','-c',f'core.hooksPath={hooks}','-c','core.longpaths=true','-c','commit.gpgsign=false',
        '-c','user.name=IC Forge','-c','user.email=icforge@localhost',*args],cwd=root,
        input=data,capture_output=True,timeout=30,env=env)
    if result.returncode:
        raise ValueError('Git 操作失败：'+redact(result.stderr.decode('utf-8',errors='replace')[-2500:],env))
    return result.stdout


def relative(value: str) -> str:
    if not isinstance(value,str) or not value or any(c in value for c in ('\0','\n','\r',':')):
        raise ValueError('文件范围必须为工作区相对路径。')
    value = value.replace('\\','/').rstrip('/')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or value in {'','.','*','**'}:
        raise ValueError('分工文件范围过宽或越界。')
    if any(p in {'.git','.fpga-agent','.tools','.venv'} or p.startswith('.env') for p in path.parts):
        raise ValueError('分工不能修改内部状态、工具或凭据。')
    if path.name == ASSIGNMENT or any(word in path.name.lower() for word in ('credential','password','secret')):
        raise ValueError('不能导入任务控制文件或凭据。')
    return value


def allowed(path: str, scopes: list[str]) -> bool:
    return any(path == scope or path.startswith(scope+'/') or fnmatch.fnmatchcase(path,scope) for scope in scopes)


def overlap(left: list[str], right: list[str]) -> bool:
    for a in left:
        for b in right:
            a,b = re.split(r'[\*?\[]',a,maxsplit=1)[0], re.split(r'[\*?\[]',b,maxsplit=1)[0]
            if a.startswith(b) or b.startswith(a):
                return True
    return False


def validate_jobs(jobs: list, executors: list[str], catalog: list[dict]) -> list[dict]:
    if not isinstance(jobs,list) or not 1 <= len(jobs) <= 8:
        raise ValueError('每批须为 1..8 个分工。')
    installed = {item['name'] for item in catalog if item['available'] and item['name'] in executors}
    result,ids = [],set()
    for raw in jobs:
        if not isinstance(raw,dict):
            raise ValueError('分工必须是对象。')
        item = dict(raw)
        name,role = item.get('id'),item.get('role')
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}',name) or name in ids:
            raise ValueError('分工 ID 必须唯一并使用小写字母/数字/连字符。')
        if role not in {'implement','analysis','review'} or item.get('complexity') not in {'small','medium','large'}:
            raise ValueError('分工角色或复杂度无效。')
        if not isinstance(item.get('prompt'),str) or not 1 <= len(item['prompt']) <= 16000:
            raise ValueError('分工说明须为 1..16,000 字符。')
        files,deps = item.get('files'),item.get('depends_on')
        if not isinstance(files,list) or not all(isinstance(f,str) for f in files) or len(files)>30:
            raise ValueError('files 必须为最多 30 个路径范围。')
        item['files'] = [relative(f) for f in files]
        if role == 'implement' and not files:
            raise ValueError('实现分工必须声明可修改文件范围。')
        if role != 'implement' and files:
            raise ValueError('分析/审查分工为只读，files 应为空。')
        if not isinstance(deps,list) or not all(isinstance(d,str) for d in deps):
            raise ValueError('depends_on 必须为分工 ID 列表。')
        executor = item.get('executor','auto')
        if executor == 'auto':
            executor = next((backend for backend in BACKENDS if backend in installed),'')
        if executor not in installed:
            raise ValueError(f'没有可用的执行者 {executor or "auto"}；已安装且启用：{sorted(installed)}')
        item.update(executor=executor,status='pending',attempts=0,failure_count=0,launcher_recoveries=0)
        ids.add(name)
        result.append(item)
    if any(dep not in ids for job in result for dep in job['depends_on']):
        raise ValueError('分工引用了不存在的依赖。')
    order,remaining = [],list(result)
    while remaining:
        ready = [item for item in remaining if set(item['depends_on']) <= set(order)]
        if not ready:
            raise ValueError('分工依赖存在环，不能执行。')
        for item in ready:
            order.append(item['id']); remaining.remove(item)
    by_id = {item['id']:item for item in result}
    for index,name in enumerate(order):
        item = by_id[name]
        for earlier in order[:index]:
            if overlap(item['files'],by_id[earlier]['files']) and earlier not in item['depends_on']:
                item['depends_on'].append(earlier)
    for item in result:
        if item['role']=='review' and any(other['role']=='implement' for other in result):
            ancestors=set(item['depends_on'])
            for _ in result:
                ancestors |= {dep for name in list(ancestors) for dep in by_id[name]['depends_on']}
            if not any(by_id[name]['role']=='implement' for name in ancestors):
                raise ValueError('审查分工必须依赖本批实现分工，确保看到新结果。')
    return result


def file_state(root: Path, name: str):
    path = root/name
    if path.is_symlink() or path.resolve()!=path.absolute():
        raise ValueError('编排导入不支持符号链接：'+name)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 16_000_000:
        raise ValueError('文件不是普通文件或超过 16 MB：'+name)
    return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'executable':bool(path.stat().st_mode & 0o100) if os.name!='nt' else False}


def source_paths(root: Path, scopes: list[str] = ()) -> list[str]:
    tracked = [p for p in git(root,'ls-files','-z').decode('utf-8').split('\0') if p]
    folders = {p.split('/')[0] for p in tracked if '/' in p}
    extra = [p for p in git(root,'ls-files','--others','--exclude-standard','-z').decode('utf-8').split('\0') if p]
    # Preserve relevant uncommitted source; unrelated top-level user directories are not copied.
    names = sorted(set(tracked+[p for p in extra if '/' not in p or p.split('/')[0] in folders or allowed(p,scopes)]))
    selected = []
    for name in names:
        try:
            relative(name)
            selected.append(name)
        except ValueError:
            continue
    if len(selected)>10000:
        raise ValueError('源码快照超过 10,000 个文件。')
    return selected


def fingerprint(root: Path, scopes: list[str] = ()) -> dict:
    names = source_paths(root,scopes)
    # Keep deleted, newly imported untracked paths in the acceptance contract as well.
    names = sorted(set(names) | {scope for scope in scopes if not any(c in scope for c in '*?[') and not (root/scope).is_dir()})
    if sum((root/name).stat().st_size for name in names if (root/name).is_file())>128_000_000:
        raise ValueError('源码快照超过 128 MB。')
    return {name:file_state(root,name) for name in names}


def imported_paths(task: dict) -> list[str]:
    return sorted({name for batch in task.get('delegations',[]) for name in batch.get('changed_files',[])})


def validate_checks(value) -> list[dict]:
    if not isinstance(value,list) or not 1 <= len(value) <= 10:
        raise ValueError('workspace 验收须预先声明 1..10 个宿主检查。')
    result=[]
    for index,item in enumerate(value):
        if not isinstance(item,dict) or not isinstance(item.get('argv'),list) or not item['argv'] or not all(isinstance(a,str) and '\0' not in a for a in item['argv']):
            raise ValueError('检查必须包含非空 argv 字符串数组，直接执行而非 shell。')
        argv=list(item['argv'])
        if argv[0] in {'python','python3','{python}'}:
            argv[0]=sys.executable
        if Path(argv[0]).suffix.lower() in {'.cmd','.bat','.ps1'}:
            raise ValueError('检查请使用可直接执行的程序，例如 python.exe 或 node.exe。')
        timeout=item.get('timeout_seconds',1800)
        if type(timeout) is not int or not 1 <= timeout <= 28800:
            raise ValueError('检查超时须为 1..28,800 秒。')
        result.append({'name':str(item.get('name',f'check-{index+1}'))[:100], 'argv':argv,'timeout_seconds':timeout})
    return result


def workspace_gate(root: Path, task: dict) -> tuple[bool,str]:
    evidence=task.get('workspace_evidence',{})
    if not task.get('checks') or evidence.get('status')!='pass':
        return False,'工作区宿主检查尚未全部通过。'
    if evidence.get('checks')!=task['checks'] or evidence.get('inputs')!=fingerprint(root,imported_paths(task)):
        return False,'工作区输入或验收配置在检查后变化，需重新 workspace-verify。'
    return True,'当前源码已通过预先声明的全部宿主检查。'


async def verify_workspace(engine) -> dict:
    checks=validate_checks(engine.task.get('checks'))
    scopes=imported_paths(engine.task)
    before=fingerprint(engine.workspace.root,scopes)
    rows=[]
    for check in checks:
        started=time.monotonic()
        try:
            code=await asyncio.wait_for(engine.command(check['argv']),check['timeout_seconds'])
        except asyncio.TimeoutError:
            code=124
        rows.append({'name':check['name'],'exit_code':code,'elapsed_seconds':round(time.monotonic()-started,3)})
        if code or engine.stop_reason():
            break
    passed=len(rows)==len(checks) and all(row['exit_code']==0 for row in rows) and before==fingerprint(engine.workspace.root,scopes)
    result={'status':'pass' if passed else 'fail','checks':checks,'results':rows,'inputs':before,'finished_at':timestamp()}
    engine.task['workspace_evidence']=result
    write_json(engine.directory/'workspace-evidence.json',result)
    engine.save()
    return {key:result[key] for key in ('status','results','finished_at')}


class Orchestrator:
    def __init__(self,engine):
        self.engine=engine
        self.root=engine.workspace.root
        self.config=engine.task.get('orchestration',{})
        self.runners: dict[str,ProcessRunner]={}

    def save(self,batch):
        write_json(self.engine.directory/'delegations'/batch['id']/'batch.json',batch)
        summary={key:batch.get(key) for key in ('id','status','reason','changed_files','created_at','scheduling')}
        summary['jobs']=[{key:job.get(key) for key in ('id','role','executor','complexity','status','attempts','failure_count','launcher_recoveries','session_id','error')} for job in batch['jobs']]
        summaries=self.engine.task.setdefault('delegations',[])
        self.engine.task['delegations']=[item for item in summaries if item['id']!=batch['id']]+[summary]
        self.engine.save()

    def prepare(self,jobs,batch_id):
        if not self.config.get('enabled'):
            raise ValueError('先选择 orchestrate 模式，再调用外部 agent。')
        if Path(git(self.root,'rev-parse','--show-toplevel').decode().strip()).resolve()!=self.root:
            raise ValueError('编排工作区必须为 Git 仓库根目录。')
        directory=self.engine.directory/'delegations'
        if batch_id:
            if not re.fullmatch(r'batch-\d{4}',batch_id):
                raise ValueError('批次 ID 格式无效。')
            batch=read_json(directory/batch_id/'batch.json')
            if not isinstance(batch,dict):
                raise ValueError('批次不存在。')
            if jobs:
                old={item['id']:item for item in batch['jobs']}
                if not isinstance(jobs,list) or any(not isinstance(item,dict) or not isinstance(item.get('id'),str) for item in jobs):
                    raise ValueError('重试分工格式无效。')
                requested={item['id']:item for item in jobs}
                if len(requested)!=len(jobs) or any(name not in old or old[name]['status']=='completed' for name in requested):
                    raise ValueError('只能更新现有未完成分工，ID 不可重复。')
                keys=('id','executor','role','complexity','prompt','files','depends_on')
                # Validate against the entire existing DAG: retry nodes can depend on completed nodes.
                candidates=[requested.get(item['id'],{key:item[key] for key in keys}) for item in batch['jobs']]
                updates=[item for item in validate_jobs(candidates,self.config['executors'],available()) if item['id'] in requested]
                for update in updates:
                    if update['id'] not in old or old[update['id']]['status']=='completed':
                        raise ValueError('只能更新现有未完成分工。')
                    previous=old[update['id']]
                    if set(update['files'])!=set(previous['files']) or set(update['depends_on'])!=set(previous['depends_on']) or update['role']!=previous['role']:
                        raise ValueError('重试保留已声明的角色、文件范围和依赖；仅更新说明或执行者。')
                    if previous['executor']!=update['executor']:
                        previous.pop('session_id',None)
                    previous.update(prompt=update['prompt'],executor=update['executor'])
            return batch
        unresolved=[item for item in self.engine.task.get('delegations',[]) if item['status'] not in {'integrated'}]
        if unresolved:
            raise ValueError('先恢复或重试未完成批次：'+unresolved[-1]['id'])
        validated=validate_jobs(jobs,self.config['executors'],available())
        batch_id=f"batch-{len(self.engine.task.get('delegations',[]))+1:04d}"
        parent=directory/batch_id
        parent.mkdir(parents=True,exist_ok=True)
        integration=self.engine.workspace.state/'trees'/self.engine.task['id']/batch_id/'integration'
        integration.parent.mkdir(parents=True,exist_ok=True)
        scopes=imported_paths(self.engine.task)+[scope for job in validated for scope in job['files']]
        before=fingerprint(self.root,scopes)
        head=git(self.root,'rev-parse','HEAD').decode().strip()
        git(self.root,'worktree','add','--detach',str(integration),head)
        # Snapshot the user's existing source without staging or resetting their main worktree.
        for name,state in before.items():
            source,target=self.root/name,integration/name
            if state is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,target)
        git(integration,'add','-A')
        if git(integration,'diff','--cached','--name-only').strip():
            git(integration,'commit','-m','IC Forge initial source snapshot')
        base=git(integration,'rev-parse','HEAD').decode().strip()
        batch={'id':batch_id,'status':'pending','reason':'等待分工执行','created_at':timestamp(),
               'jobs':validated,'integration':str(integration),'base_commit':base,'main_inputs':before,'changed_files':[]}
        self.save(batch)
        return batch

    def prompt(self,batch,job):
        dependencies=[{'id':item['id'],'report':item.get('report','')[-5000:]} for item in batch['jobs'] if item['id'] in job['depends_on']]
        platform_advice=('Use terminal commands only. On Windows do not pipe patches into apply_patch: this launcher rejects piped UTF-8. '
            'Use the native apply_patch tool when exposed, or .NET File.WriteAllText with UTF8Encoding(false) and literal here-strings. '
            'Keep scratch/test-output files in ignored build/ directories. Host already validated the requested model and CLI; '
            'do not repeat model setup or installation research for an unrelated coding subtask.\n') if os.name=='nt' else ''
        return ('You are one worker directed by IC Forge. Finish only your assigned subtask.\n'
            'Do not spawn other agents, push/publish, change credentials, reset branches, or edit .git/.fpga-agent.\n'
            'Keep trusted testbenches/reference contracts intact unless this task explicitly revises them.\n'
            'Implementation changes will be imported only from the declared file scopes. Analysis/review is read-only.\n'
            'Do not claim validation you did not run. Summarize changes, reasoning, evidence and remaining issues.\n'
            'The host runs independent acceptance checks after integration.\n'+platform_advice+json.dumps({
                'objective':self.engine.task['objective'],'steering':self.engine.task.get('steering',[]),
                'job':{key:job[key] for key in ('id','role','prompt','files','complexity')},
                'dependencies':dependencies,'previous_report':job.get('report','')[-5000:],
                'previous_error':job.get('error','')},ensure_ascii=False))

    async def job(self,batch,job):
        record=self.engine.directory/'delegations'/batch['id']/'jobs'/job['id']
        record.mkdir(parents=True,exist_ok=True)
        tree_root=self.engine.workspace.state/'trees'/self.engine.task['id']/batch['id']
        worktree=Path(job['worktree']) if job.get('worktree') else tree_root/'workers'/job['id']
        if not worktree.resolve().is_relative_to(tree_root.resolve()) or worktree.resolve()==Path(batch['integration']).resolve():
            raise ValueError('子任务工作树路径越界或与集成工作树冲突；需恢复独立工作树。')
        worktree.parent.mkdir(parents=True,exist_ok=True)
        job['worktree']=str(worktree)
        if job.get('failure_count',0)>=3:
            job.update(status='failed',error='该分工已达到 3 次失败上限；暂停不计入失败。')
            self.save(batch)
            return
        if not worktree.exists():
            base=git(Path(batch['integration']),'rev-parse','HEAD').decode().strip()
            git(self.root,'worktree','add','--detach',str(worktree),base)
            job['base_commit']=base
        self.engine.consume_steering()
        assignment=self.prompt(batch,job)
        (worktree/ASSIGNMENT).write_text(assignment,encoding='utf-8')
        job.update(status='running',attempts=job.get('attempts',0)+1,started_at=timestamp(),error=None)
        attempt=record/f"attempt-{job['attempts']}"
        attempt.mkdir(exist_ok=True)
        (attempt/'assignment.txt').write_text(redact(assignment,dict(os.environ)),encoding='utf-8')
        native=Events(job['executor'])
        runner=ProcessRunner()
        self.runners[job['id']]=runner
        self.save(batch)
        env=environment(dict(os.environ),self.engine.task['settings'].get('base_url',''))
        gateway=None
        try:
            argv,overrides=build_command(job['executor'],worktree,attempt,role=job['role'],
                model=self.config.get('models',{}).get(job['executor'],''),session_id=job.get('session_id',''))
            if job['executor']=='claude':
                gateway=ClaudeGateway.configured(env)
                if gateway:
                    overrides.update(gateway.start())
                    settings_path=attempt/'gateway-settings.json'
                    write_json(settings_path,{'env':{key:overrides[key] for key in ('ANTHROPIC_BASE_URL','ANTHROPIC_AUTH_TOKEN','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')}})
                    argv+=['--settings',str(settings_path)]
        except (ValueError,OSError) as exc:
            if gateway:
                gateway.close()
            job.update(status='failed',error=str(exc))
            job['failure_count']=job.get('failure_count',0)+1
            self.runners.pop(job['id'],None)
            self.save(batch)
            return
        env.update(overrides)
        def output(line):
            native.accept(line)
            if native.session_id and native.session_id!=job.get('session_id'):
                job['session_id']=native.session_id
                self.save(batch)
            log=attempt/'events.jsonl'
            if not log.exists() or log.stat().st_size<4_000_000:
                with log.open('a',encoding='utf-8') as stream:
                    stream.write(redact(line,env)+'\n')
            self.engine.output(f"[{job['id']}/{job['executor']}] {line}")
        def spawned(pid):
            self.engine.task.setdefault('child_pids',{})[job['id']]=pid
            job['pid']=pid
            self.save(batch)
        started=time.monotonic()
        last_heartbeat=started
        running=asyncio.create_task(runner.run(argv,worktree,env,output,spawned,
            stdin_text=assignment if job['executor']!='opencode' else None,line_limit=1_000_000))
        timeout=False
        launcher_recovery=False
        try:
            while not running.done():
                await asyncio.wait({running},timeout=0.25)
                timeout=time.monotonic()-started>=self.config.get('job_timeout_seconds',3600)
                launcher_recovery=launcher_recovery or native.launcher_failures>=3
                if self.engine.stop_reason() or timeout or launcher_recovery:
                    await runner.cancel()
                if time.monotonic()-last_heartbeat>=1:
                    self.engine.heartbeat()
                    last_heartbeat=time.monotonic()
            code=await running
            report=redact(native.result(attempt),env)
            job.update(exit_code=code,report=report,usage=native.usage,finished_at=timestamp())
            if self.engine.stop_reason():
                job.update(status='interrupted',error='任务暂停/取消或耗尽时间预算。')
            elif launcher_recovery and job.get('launcher_recoveries',0)<2:
                job.update(status='retryable',launcher_recoveries=job.get('launcher_recoveries',0)+1,
                    error='Repeated Windows patch-launcher errors. Do not pipe patches or invoke the batch wrapper again. '
                    'Use .NET File.WriteAllText with UTF8Encoding(false), literal here-strings, and the declared file scopes. '
                    'Keep scratch files in ignored build/. Resume from existing source; do not repeat installation research.')
                self.engine.event('agent_launcher_recovery',job_id=job['id'],attempt=job['attempts'])
            elif timeout or code or native.errors or not report.strip():
                job.update(status='failed',error='执行超时' if timeout else '\n'.join(native.errors)[-3000:] or f'外部 agent exit={code} 或没有最终报告。')
            else:
                self.accept_changes(batch,job,worktree,record)
        except (OSError,ValueError) as exc:
            job.update(status='failed',error=redact(str(exc),env))
        finally:
            if not running.done():
                await runner.cancel()
                await running
                job.update(status='interrupted',error='主管 worker 中断。')
            self.engine.task.setdefault('child_pids',{}).pop(job['id'],None)
            if job['status']=='failed':
                job['failure_count']=job.get('failure_count',0)+1
            self.runners.pop(job['id'],None)
            if gateway:
                gateway.close()
            self.save(batch)

    def accept_changes(self,batch,job,worktree,record):
        base=job['base_commit']
        changed=[p for p in git(worktree,'diff','--no-ext-diff','--no-textconv','--no-renames',base,'--name-only','-z').decode('utf-8').split('\0') if p]
        changed += [p for p in git(worktree,'ls-files','--others','--exclude-standard','-z').decode('utf-8').split('\0') if p and p!=ASSIGNMENT]
        changed=sorted(set(changed)-{ASSIGNMENT})
        for name in changed:
            relative(name)
            file_state(worktree,name)
            if job['role']!='implement' or not allowed(name,job['files']):
                raise ValueError('agent 修改了分工范围之外的文件：'+name)
        if job['role']=='implement' and not changed:
            raise ValueError('实现分工没有可导入的变更，不能仅凭报告标记完成。')
        for index in range(0,len(changed),50):
            git(worktree,'add','-A','--',*changed[index:index+50])
        patch=git(worktree,'diff','--no-ext-diff','--no-textconv','--no-renames','--binary',base)
        if len(patch)>32_000_000:
            raise ValueError('分工补丁超过 32 MB。')
        (record/'changes.patch').write_bytes(patch)
        job.update(status='completed',changed_files=changed,patch_sha256=hashlib.sha256(patch).hexdigest(),
                   expected={name:file_state(worktree,name) for name in changed})

    def merge_wave(self,batch,wave):
        integration=Path(batch['integration'])
        for job in wave:
            if job['status']!='completed' or job.get('merged'):
                continue
            if {name:file_state(integration,name) for name in job['changed_files']}==job['expected']:
                job['merged']=True
                self.save(batch)
                continue
            patch=(self.engine.directory/'delegations'/batch['id']/'jobs'/job['id']/'changes.patch').read_bytes()
            if patch:
                git(integration,'apply','--check','--binary','-',data=patch)
                git(integration,'apply','--binary','-',data=patch)
                git(integration,'add','-A')
                git(integration,'commit','-m','IC Forge scoped subtask '+job['id'])
            job['merged']=True
            self.save(batch)

    def integrate(self,batch):
        integration=Path(batch['integration'])
        if not batch.get('integration_patch'):
            patch=git(integration,'diff','--no-ext-diff','--no-textconv','--no-renames','--binary',batch['base_commit'])
            path=self.engine.directory/'delegations'/batch['id']/'integration.patch'
            path.write_bytes(patch)
            changed=[p for p in git(integration,'diff','--no-renames',batch['base_commit'],'--name-only','-z').decode('utf-8').split('\0') if p]
            batch.update(integration_patch=str(path),changed_files=changed,
                expected={name:file_state(integration,name) for name in changed})
            self.save(batch)
        changed=batch['changed_files']
        current={name:file_state(self.root,name) for name in changed}
        baseline={name:batch['main_inputs'].get(name) for name in changed}
        if current==batch['expected']:
            batch.update(status='integrated',reason='变更已在主工作区，恢复确认通过。')
            self.save(batch)
            return
        if current!=baseline:
            raise ValueError('主工作区相关文件在分工期间发生变化，拒绝覆盖；候选工作树已保留。')
        patch=Path(batch['integration_patch']).read_bytes()
        batch.update(status='applying',reason='正在导入已审查范围的补丁')
        self.save(batch)
        if patch:
            git(self.root,'apply','--check','--binary','-',data=patch)
            git(self.root,'apply','--binary','-',data=patch)
        if {name:file_state(self.root,name) for name in changed}!=batch['expected']:
            raise ValueError('导入后文件指纹不匹配。')
        batch.update(status='integrated',reason='分工变更已导入，等待宿主独立验收。')
        self.save(batch)

    async def schedule(self,batch):
        """Fill free slots from ready dependencies; a slow peer must not form a wave barrier."""
        running={}
        metrics=batch.setdefault('scheduling',{'policy':'dependency-ready','max_parallel_observed':0,'dispatch_order':[]})
        failed=False
        try:
            while not self.engine.stop_reason():
                complete={item['id'] for item in batch['jobs'] if item['status']=='completed' and item.get('merged')}
                ready=[item for item in batch['jobs'] if item['status']=='pending' and item['id'] not in running and set(item['depends_on'])<=complete]
                if not failed:
                    for item in ready[:max(0,self.config['max_parallel']-len(running))]:
                        future=asyncio.create_task(self.job(batch,item))
                        running[item['id']]=(future,item)
                        metrics['dispatch_order']=(metrics['dispatch_order']+[item['id']])[-64:]
                        metrics['max_parallel_observed']=max(metrics['max_parallel_observed'],len(running))
                        self.engine.event('agent_dispatched',batch_id=batch['id'],job_id=item['id'],executor=item['executor'],
                            model=self.config.get('models',{}).get(item['executor'],'native'),active_slots=len(running),
                            depends_on=item['depends_on'])
                        self.save(batch)
                if not running:
                    break
                done,_=await asyncio.wait([pair[0] for pair in running.values()],timeout=.25,return_when=asyncio.FIRST_COMPLETED)
                for name,(future,item) in list(running.items()):
                    if future not in done:
                        continue
                    await future
                    del running[name]
                    if item['status']=='retryable':
                        item['status']='pending'
                        self.save(batch)
                    self.merge_wave(batch,[item])
                    failed=failed or item['status']=='failed'
                    self.engine.event('agent_finished',batch_id=batch['id'],job_id=name,status=item['status'],active_slots=len(running))
                # After a failed node, retain already running independent work but launch no new work.
        finally:
            if running:
                await asyncio.gather(*(runner.cancel() for runner in list(self.runners.values())))
                await asyncio.gather(*(pair[0] for pair in running.values()),return_exceptions=True)
    async def run(self,jobs: list, batch_id: str = '') -> dict:
        batch_id=batch_id or self.engine.task.get('active_delegation','')
        batch=self.prepare(jobs,batch_id)
        if batch['status']=='integrated':
            return self.report(batch)
        self.engine.task['active_delegation']=batch['id']
        self.save(batch)
        try:
            if batch['status']=='applying':
                self.integrate(batch)
            else:
                for job in batch['jobs']:
                    job.setdefault('failure_count',job.get('attempts',0) if job['status']=='failed' else 0)
                    if job['status'] in {'running','interrupted','failed','retryable'}:
                        job['status']='pending'
                batch.update(status='running',reason='按依赖关系执行')
                self.save(batch)
                self.merge_wave(batch,batch['jobs'])
                await self.schedule(batch)
                if self.engine.stop_reason():
                    batch.update(status='interrupted',reason='分工状态已保存，恢复时继续未完成节点。')
                elif all(job['status']=='completed' and job.get('merged') for job in batch['jobs']):
                    self.integrate(batch)
                else:
                    batch.update(status='failed',reason='存在失败分工；指定 batch_id 重试，可调整未完成分工说明或执行者。')
        except (OSError,ValueError) as exc:
            batch.update(status='failed',reason=redact(str(exc),dict(os.environ)))
        except BaseException:
            batch.update(status='interrupted',reason='主管 worker 中断，保留工作树和分工状态。')
            raise
        finally:
            if self.runners:
                await asyncio.gather(*(runner.cancel() for runner in self.runners.values()))
            if batch['status']!='interrupted':
                self.engine.task.pop('active_delegation',None)
            self.save(batch)
        return self.report(batch)

    def report(self,batch):
        return {'batch_id':batch['id'],'status':batch['status'],'reason':batch['reason'],'changed_files':batch.get('changed_files',[]),
            'scheduling':batch.get('scheduling',{}),
            'jobs':[{key:job.get(key) for key in ('id','executor','role','status','depends_on','changed_files','session_id','error','usage')}
                    | {'report':job.get('report','')[-3000:]} for job in batch['jobs']]}
