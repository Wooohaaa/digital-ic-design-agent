"""Headless adapters for installed coding agents; prompts never enter a shell."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

BACKENDS = ('codex','claude','opencode')


def command_prefix(name: str) -> list[str] | None:
    if name not in BACKENDS:
        raise ValueError('不支持的外部 agent。')
    found = shutil.which(name)
    if not found:
        return None
    path = Path(found)
    if os.name != 'nt' or path.suffix.lower() not in {'.cmd','.bat','.ps1'}:
        return [str(path)]
    # Avoid cmd.exe quoting/execution of batch shims, especially with model text.
    if name == 'claude':
        native = path.parent/'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
        if native.is_file():
            return [str(native)]
    packages = {'codex':'@openai/codex/bin/codex.js',
                'claude':'@anthropic-ai/claude-code/cli.js','opencode':'opencode-ai/bin/opencode'}
    entry = path.parent/'node_modules'/packages[name]
    node = shutil.which('node.exe') or shutil.which('node')
    return [node,str(entry)] if node and entry.is_file() else None


def available() -> list[dict]:
    return [{'name':name,'available':bool(prefix := command_prefix(name)),
             'command':prefix or [],'roles':['implement','analysis','review']} for name in BACKENDS]


def environment(parent: dict, supervisor_base_url: str = '') -> dict:
    env = dict(parent)
    # A supervisor's compatible gateway/key must not silently replace CLI-native login.
    for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','FPGA_AGENT_API_KEY','FPGA_AGENT_EXCHANGE_RESPONSE_FILE'):
        env.pop(key,None)
    for key,value in parent.items():
        if key.startswith('ICFORGE_EXECUTOR_'):
            env[key.removeprefix('ICFORGE_EXECUTOR_')] = value
    env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1',PYTHONUNBUFFERED='1')
    return env


def build_command(name: str, directory: Path, record: Path, *, role: str,
                  model: str = '', session_id: str = '', prefix: list[str] | None = None) -> tuple[list[str],dict]:
    if name not in BACKENDS or role not in {'implement','analysis','review'}:
        raise ValueError('agent 或分工角色无效。')
    prefix = prefix if prefix is not None else command_prefix(name)
    if not prefix:
        raise ValueError(f'未找到可直接执行的 {name} CLI；请先安装或修复 PATH。')
    if session_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',session_id):
        raise ValueError('外部会话 ID 格式无效。')
    writing = role == 'implement'
    options = {}
    if name == 'codex':
        argv = prefix + ['-a','never','-s','workspace-write' if writing else 'read-only','-C',str(directory),'exec']
        if session_id:
            argv += ['resume',session_id]
        argv += ['--json','--output-last-message',str(record/'last-message.txt')]
        if model:
            argv += ['--model',model]
        argv += ['-']
    elif name == 'claude':
        tools = 'Read,Glob,Grep,Write,Edit' if writing else 'Read,Glob,Grep'
        argv = prefix + ['-p','--verbose','--output-format','stream-json',
            '--permission-mode','acceptEdits' if writing else 'default','--tools',tools,
            '--strict-mcp-config','--mcp-config','{"mcpServers":{}}']
        if model:
            argv += ['--model',model]
        if session_id:
            argv += ['--resume',session_id]
    else:
        argv = prefix + ['run','Read the attached IC Forge assignment and finish your scoped subtask.','--pure','--format','json','--dir',str(directory),
            '--agent','build' if writing else 'plan']
        if model:
            argv += ['--model',model]
        if session_id:
            argv += ['--session',session_id]
        argv += ['--file',str(directory/'.icforge-assignment.md')]
        options['OPENCODE_CONFIG_CONTENT'] = json.dumps({'share':'disabled','permission':{
            '*':'deny','read':'allow','glob':'allow','grep':'allow','list':'allow',
            'edit':'allow' if writing else 'deny','external_directory':'deny','bash':'deny','task':'deny'}})
    return argv,options


class Events:
    """Collect native IDs, final text and explicit logical errors, independent of exit code."""
    def __init__(self,name: str):
        self.name = name
        self.session_id = ''
        self.text: list[str] = []
        self.errors: list[str] = []
        self.usage: dict[str,int] = {}
        self.launcher_failures = 0

    def accept(self,line: str):
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event,dict):
            return
        candidate = event.get('thread_id') or event.get('session_id') or event.get('sessionID')
        if isinstance(candidate,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',candidate):
            self.session_id = candidate
        kind = event.get('type','')
        item = event.get('item',{})
        if kind=='item.completed' and isinstance(item,dict) and item.get('type')=='command_execution':
            command=str(item.get('command',''));output=str(item.get('aggregated_output',''))
            patch_command='apply_patch' in command or 'codex-run-as-apply-patch' in command
            if patch_command and item.get('exit_code') not in (None,0) and any(marker in output.lower() for marker in (
                'requires a utf-8 patch argument','last line of the patch must be','command line is too long')):
                self.launcher_failures+=1
            elif item.get('exit_code')==0 and ('Success. Updated the following files' in output or 'WriteAllText' in command):
                self.launcher_failures=0
        if kind == 'item.completed' and isinstance(item,dict) and item.get('type') == 'agent_message':
            self.text.append(str(item.get('text','')))
        if kind == 'result':
            if event.get('is_error') or str(event.get('subtype','')).startswith('error'):
                self.errors.append(str(event.get('result') or event.get('errors') or 'agent result error'))
            elif isinstance(event.get('result'),str):
                self.text.append(event['result'])
        if kind == 'text' and isinstance(event.get('part'),dict):
            self.text.append(str(event['part'].get('text','')))
        if kind in {'error','turn.failed'}:
            self.errors.append(str(event.get('message') or event.get('error') or 'agent stream error'))
        usage = event.get('usage') if kind in {'turn.completed','result'} else None
        if not usage and kind == 'step_finish':
            usage = event.get('part',{}).get('tokens')
        if isinstance(usage,dict):
            for key,value in usage.items():
                if type(value) is int and value >= 0:
                    self.usage[key] = self.usage.get(key,0) + value

    def result(self,record: Path) -> str:
        path = record/'last-message.txt'
        if self.name == 'codex' and path.is_file():
            return path.read_text(encoding='utf-8',errors='replace')[:24000]
        return '\n'.join(self.text)[-24000:]
