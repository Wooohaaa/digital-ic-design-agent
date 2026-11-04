"""Bounded, read-only source inspection, portable skills and historical recall."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .terminal import Workspace, read_json, redact

READ_ACTIONS = {"inspect", "search", "skill", "recall", "unload-skill"}
TEXT_SUFFIXES = {".v", ".sv", ".sva", ".md", ".json", ".py", ".c", ".h", ".cpp", ".tcl",
                 ".sdc", ".adc", ".txt", ".toml", ".yaml", ".yml", ".log", ".hex", ".mem"}
SKILL_DIRS = (".icforge/skills", ".agents/skills", ".opencode/skills", ".claude/skills", ".commandcode/skills", ".hermes/skills")
SECRET_ASSIGNMENT = re.compile(r'''(\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|authorization)\b["']?\s*[:=]\s*)(["'])([^"'\r\n]*)\2''',re.I)


def scrub(value):
    if isinstance(value, str):
        value = redact(value, dict(os.environ))
        return SECRET_ASSIGNMENT.sub(lambda m:m[1]+m[2]+'[redacted]'+m[2],value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def safe_text(path: Path, root: Path) -> Path:
    root, path = root.resolve(), path.resolve()
    if root != path and root not in path.parents:
        raise ValueError("读取路径必须在允许的目录内。")
    relative = path.relative_to(root)
    if any(p.lower() in {".git", ".fpga-agent", ".tools"} for p in relative.parts):
        raise ValueError("内部状态通过 recall 读取，不能直接扫描凭据或工具目录。")
    name = path.name.lower()
    if name.startswith(".env") or any(word in name for word in ("credential", "secret", "password")) or name in {"auth.json", "tokens.json"}:
        raise ValueError("该文件可能包含凭据，不能作为模型上下文。")
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        raise ValueError("只能读取存在的工程文本文件。")
    return path


def read_excerpt(path: Path, *, offset: int = 1, limit: int = 120) -> dict:
    if type(offset) is not int or offset < 1 or type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("offset 从 1 开始，limit 必须为 1..200 行。")
    if path.stat().st_size > 16_000_000:
        raise ValueError("文本超过 16 MB，请使用更小的报告。")
    # Reading a bounded file also allows a hash that identifies the exact observed version.
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if "\x00" in text:
        raise ValueError("文件包含二进制数据。")
    lines = text.splitlines()
    selected, used = [], 0
    for number in range(offset - 1, min(len(lines), offset - 1 + limit)):
        line = f"{number+1}: {lines[number]}"
        if used + len(line) > 10000:
            if not selected:
                raise ValueError("单行超过读取预算，请查看结构化报告或缩小原始数据。")
            break
        selected.append(line)
        used += len(line) + 1
    next_offset = offset + len(selected)
    return scrub({"sha256": hashlib.sha256(raw).hexdigest(), "total_lines": len(lines), "offset": offset,
                  "content": "\n".join(selected), "next_offset": next_offset if next_offset <= len(lines) else None})


def _metadata(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md 必须包含 name/description frontmatter。")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("技能 frontmatter 未关闭。") from None
    fields = {}
    for i, line in enumerate(lines[1:end], 1):
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if key not in {"name", "description"}:
            continue
        if value in {">", "|", ">-", "|-"}:
            continuation = []
            for following in lines[i+1:end]:
                if following and not following[0].isspace():
                    break
                continuation.append(following.strip())
            value = " ".join(continuation)
        elif value.startswith('"') and value.endswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        fields[key] = value
    name, description = fields.get("name", ""), fields.get("description", "")
    if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("技能 name 必须是小写字母/数字及单个连字符。")
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        raise ValueError("技能 description 必须为 1..1024 字符。")
    return name, description


class SkillCatalog:
    def __init__(self, workspace: Workspace, project: Path | None = None, *, home: Path | None = None):
        self.workspace = workspace
        self.entries: list[dict] = []
        self.issues: list[dict] = []
        home = home or Path.home()
        roots = [("bundled", Path(__file__).parent / "templates/skills")]
        for label, base in (("project", project), ("workspace", workspace.root), ("user", home)):
            if base is None or (label == "project" and base.resolve() == workspace.root):
                continue
            for relative in SKILL_DIRS:
                roots.append((f"{label}/{relative.split('/')[0]}", base / relative))
        roots.append(("user/opencode-config", home / ".config/opencode/skills"))
        seen = set()
        for source, root in roots:
            for candidate in sorted(root.glob("*/SKILL.md"))[:100]:
                try:
                    path = safe_text(candidate, root)
                    if path in seen:
                        continue
                    seen.add(path)
                    if path.stat().st_size > 64000:
                        raise ValueError("技能文件超过 64 KB。")
                    raw = path.read_bytes()
                    name, description = _metadata(raw.decode("utf-8"))
                    if name != candidate.parent.name:
                        raise ValueError("技能 name 必须匹配目录名。")
                    self.entries.append({"id": source + "/" + name, "name": name,
                        "description": description, "source": source, "sha256": hashlib.sha256(raw).hexdigest(),
                        "path": str(path), "root": str(root.resolve())})
                except (OSError, ValueError, UnicodeError) as exc:
                    self.issues.append({"source": source, "file": str(candidate), "error": str(exc)})
                if len(self.entries) >= 100:
                    return

    def summary(self) -> dict:
        return scrub({"skills": [{k: v for k, v in item.items() if k not in {"path", "root"}} for item in self.entries],
                      "issues": self.issues[:20], "limit":100, "bounded":len(self.entries)>=100})

    def load(self, query: str, resource: str = "", *, offset: int = 1, limit: int = 120) -> dict:
        matches = [item for item in self.entries if query in {item["id"], item["name"]}]
        if not matches:
            candidates = [item for item in self.entries if query.casefold() in (item['id']+' '+item['description']).casefold()]
            if candidates:
                return scrub({"skills": [{k:v for k,v in item.items() if k not in {'path','root'}} for item in candidates[:20]],
                              "note": "选择完整 ID 再次调用 skill 以加载正文。"})
        if len(matches) != 1:
            raise ValueError("技能不存在或同名冲突，请用 /skills 中的完整 ID：" + ", ".join(i["id"] for i in matches))
        item = matches[0]
        path = safe_text(Path(item["path"]), Path(item["root"]))
        if resource:
            path = safe_text(path.parent / resource, path.parent)
            return {"id": item["id"], "resource": resource, **read_excerpt(path, offset=offset, limit=limit)}
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if len(text) > 16000:
            raise ValueError("技能超过 16,000 字符，请指定 path=SKILL.md 分页读取。")
        return scrub({"id": item["id"], "name": item["name"], "source": item["source"],
                      "sha256": hashlib.sha256(raw).hexdigest(), "content": text})


def recall(workspace: Workspace, query: str, *, exclude: str = "", limit: int = 5,
           resource: str = "", offset: int = 1, lines: int = 120) -> dict:
    if not query.strip() or len(query) > 200:
        raise ValueError("检索词不能为空且不能超过 200 字符。")
    reference = re.fullmatch(r'([0-9a-f]{12}):(\d{1,5})', query)
    if reference:
        directory = workspace.state/'tasks'/reference[1]/f'step-{int(reference[2]):04d}'
        if resource:
            if resource not in {'output.log','result.json','decision.json','request.json','context-full.json'}:
                raise ValueError('不支持的历史步骤资源文件。')
            path = directory/resource
            if not path.is_file():
                raise ValueError('该步骤没有所请求的归档文件。')
            return {'reference':query, 'resource':resource, 'historical_only':True,
                    **read_excerpt(path,offset=offset,limit=lines)}
        path = directory/'result.json'
        result = read_json(path)
        if not isinstance(result, dict):
            raise ValueError('步骤归档不存在。')
        if len(json.dumps(result,ensure_ascii=False)) > 16000:
            result = {key:result[key] for key in ('iteration','status','exit_code','started_at','finished_at','reason') if key in result}
            result['note'] = '完整记录较长，请指定 path=result.json 分页读取。'
        return scrub({'reference':query,'historical_only':True,'result':result,
                      'log_available':(directory/'output.log').is_file(),
                      'note':'原始输出可用 recall(query=当前引用,path=output.log,offset=行号) 分页读取。'})
    tokens = re.findall(r"[a-z0-9_-]{2,}|[\u4e00-\u9fff]{1,2}", query.lower())
    hits = []
    paths = sorted((workspace.state / "tasks").glob("*/task.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]
    for path in paths:
        if path.parent.name == exclude or path.stat().st_size > 2_000_000:
            continue
        task = read_json(path, {})
        if not isinstance(task, dict):
            continue
        text = task.get("objective", "") + "\n" + task.get("memory", "") + "\n" + task.get("reason", "")
        score = sum(text.lower().count(token) for token in set(tokens))
        if not score:
            continue
        hits.append({"task_id": task.get("id"), "status": task.get("status"), "objective": task.get("objective", "")[:800],
                     "memory": task.get("memory", "")[:1600], "reason": task.get("reason", "")[:800],
                     "updated_at": task.get("updated_at"), "projects": task.get("projects", []),
                     "historical_only": True, "score": score})
    return scrub({"matches": sorted(hits, key=lambda h: h["score"], reverse=True)[:limit],
                  "note": "历史记录仅提供线索，不能代替当前工程验收证据。", "searched_tasks": len(paths)})


def execute_read(workspace: Workspace, selected: Path | None, action: str, arguments: dict, *, task_id: str = "") -> dict:
    path, query = arguments.get("path", ""), arguments.get("query", "")
    if not isinstance(path, str) or not isinstance(query, str):
        raise ValueError("path/query 必须为文本。")
    offset, limit = arguments.get("offset", 1), arguments.get("limit", 120)
    def resolve(value):
        candidate = workspace.resolve(value, base=selected or workspace.root)
        if not candidate.exists() and selected:
            root_candidate = workspace.resolve(value)
            if root_candidate.exists():
                return root_candidate
        return candidate
    if action == "skill":
        return SkillCatalog(workspace, selected).load(query, path, offset=offset, limit=limit)
    if action == "unload-skill":
        if not query:
            raise ValueError("指定需要卸载的技能 ID。")
        return {"unload": query}
    if action == "recall":
        return recall(workspace, query, exclude=task_id, resource=path, offset=offset, lines=limit)
    if action == "inspect":
        if not path:
            raise ValueError("inspect 需要 path。")
        candidate = resolve(path)
        candidate = safe_text(candidate, workspace.root)
        return {"path": candidate.relative_to(workspace.root).as_posix(), **read_excerpt(candidate, offset=offset, limit=limit)}
    if action != "search" or not query.strip() or len(query) > 200:
        raise ValueError("search 需要 1..200 字符的字面检索词。")
    base = resolve(path) if path else selected or workspace.root
    if base.is_file():
        candidates = [base]
    elif base.is_dir():
        # Bounded traversal excludes implementation/tool/model trees before descending.
        candidates = []
        for count, (directory, dirs, files) in enumerate(os.walk(base)):
            if count >= 500:
                break
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in {'build','td','deliverables','model','node_modules','__pycache__'} and not (Path(directory)/d).is_symlink())
            candidates.extend(Path(directory)/name for name in sorted(files)[:1000] if Path(name).suffix.lower() in TEXT_SUFFIXES)
            if len(candidates) >= 300:
                candidates = candidates[:300]
                break
    else:
        raise ValueError("检索目录不存在。")
    matches, scanned, remaining = [], 0, 2_000_000
    for candidate in candidates:
        try:
            candidate = safe_text(candidate, workspace.root)
            size = candidate.stat().st_size
            if size > min(1_000_000, remaining):
                continue
            raw = candidate.read_bytes()
            remaining -= len(raw)
            scanned += 1
            for number, line in enumerate(raw.decode('utf-8', errors='replace').splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": candidate.relative_to(workspace.root).as_posix(), "line": number, "text": line[:300]})
                    if len(matches) >= 25:
                        break
            if len(matches) >= 25 or remaining <= 0:
                break
        except (ValueError, OSError):
            continue
    return scrub({"matches": matches, "scanned_files": scanned, "bounded": True,
                  "limits": "300 candidates, 2 MB total, 25 matches; literal case-insensitive search"})


def configure_parser(commands):
    command = commands.add_parser("knowledge", help="技能发现、按需读取和历史任务检索")
    sub = command.add_subparsers(dest="knowledge_command", required=True)
    for name in ("skills", "skill", "recall"):
        parser = sub.add_parser(name)
        parser.add_argument("--workspace", default=".")
        parser.add_argument("--project", default="")
        if name != "skills":
            parser.add_argument("query")


def dispatch(args):
    workspace = Workspace(Path(args.workspace))
    project = workspace.resolve(args.project) if args.project else None
    if args.knowledge_command == "skills":
        result = SkillCatalog(workspace, project).summary()
    elif args.knowledge_command == "skill":
        result = SkillCatalog(workspace, project).load(args.query)
    else:
        result = recall(workspace, args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
