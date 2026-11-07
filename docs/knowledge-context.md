# 技能、历史经验与上下文

持续任务现在可以主动加载流程知识、读取完整源码、搜索错误以及检索以前的任务，而不是反复依赖固定的源码片段和最近几条日志。

## 常用命令

TUI 中使用：

```text
/skills
/skill bundled/rv32-validation
/recall TinyStories
/recall 任务ID:步数
/context
/context 任务ID
/continue 任务ID --context-chars 192000
```

`/skill` 预览正文；后台 planner 根据目标选择加载技能，加载内容会保留到主动卸载或任务结束。`/context` 展示长期任务最近一次规划输入的字符用量和裁剪记录。

PowerShell 中也可以查询：

```powershell
icforge knowledge skills
icforge knowledge skill bundled/hx1p35a-delivery
icforge knowledge recall TinyStories
icforge task context
icforge task start '完成 CPU 验证和交付' --project benchmarks/rv32_multi --context-chars 96000
icforge task resume 任务ID --context-chars 192000
```

## 兼容技能

项目目录、工作区和用户目录下可以发现 `.icforge/skills`、`.agents/skills`、`.opencode/skills`、`.claude/skills`、`.commandcode/skills` 和 `.hermes/skills`；还支持用户级 `~/.config/opencode/skills`。

目录结构：

```text
.agents/skills/timing-review/SKILL.md
```

```markdown
---
name: timing-review
description: 检查 FPGA 时序、覆盖率和未关闭工程事项。
---
先阅读工程规格和独立验证记录，再检查 setup/hold、TNS 和 IO 预算。
```

元数据解析支持 `name`、`description` 的普通、引号和缩进多行字符串；复杂 YAML 扩展需改写成这些简单形式。其他字段不会作为命令执行。

目录只包含名称、描述、来源和指纹，不预先注入全部正文。默认有界发现最多 100 个有效技能，问题文件会出现在 issues 中。同名技能不静默覆盖，使用 `/skills` 显示的来源限定 ID 选择。随包技能包括 `rv32-validation`、`hx1p35a-delivery`、`tinystories-hardware`、`rtl-mutation-audit`、`agent-evaluation-loop` 和 `sta-constraint-audit`。

加载技能也可按需读取同一技能目录下的参考文本。外部技能附带的脚本不会自动运行；只有现有的受限工程工具可执行。技能或历史记录不能跳过工程验收。已加载技能发生变化或消失时，上下文会标记为过期，须重新加载。

## Planner 可用的读取工具

| 工具 | 用途与限制 |
|---|---|
| `inspect` | 按 `path` 读取工程文本；`offset` 为 1 起始行号，`limit` 最多 200 行，每页最多约 10,000 字符；返回全文指纹和下一页位置 |
| `search` | 在指定目录按字面词语检索；最多 300 个候选文件、累计 2 MB 和 25 条命中，自动排除构建和工具目录 |
| `skill` | 按技能 ID/名称加载；关键词可发现候选；指定相对 `path` 可读取技能参考文件 |
| `unload-skill` | 从当前任务上下文卸载指定技能正文，保留原步骤记录 |
| `recall` | 在最近最多 100 个任务的目标/工作记忆中做关键词检索；或按 `任务ID:步数` 读取归档 |

普通文件路径优先按当前工程解析，不存在时尝试工作区相对路径。工作区之外的读取和凭据文件会被拒绝。技能目录是单独声明的只读来源，引用资源只能留在该技能目录内。

例如读取第 5 步的旧日志：`recall(query="任务ID:5", path="output.log", offset=120, limit=80)`。可读取该步的 `result.json`、`decision.json`、`request.json` 和裁剪前的 `context-full.json`。历史返回值只提供线索，不能代替当前工程的验证证据。

## 上下文预算

默认总预算为 96,000 个 JSON 字符，预留 8,000 字符给固定提示、结构化约束和余量；这不是精确 token 计量，也不代表任何模型的真实上下文窗口。

系统优先缩减旧工程审查细节、旧工具输出和可重读源码，再减少其他目录元数据。目标、用户追加要求、工程指令、当前观察、工作记忆、计划和已加载技能完整保留。它们本身超过预算时会明确阻塞，避免悄悄丢失要求。

每次规划保留 `context-budget.json` 用量报告；发生裁剪时还保存该步的 `context-full.json`。服务端明确返回上下文过长会缩减本地预算后有限重试；普通 502 不触发上下文缩减。可通过 `--context-chars` 调整预算，范围为 16,000..2,000,000 字符。

每步输出另存 `step-NNNN/output.log`，上限约 4 MB；汇总日志上限约 20 MB。达到上限会写入 `ICFORGE_LOG_LIMIT` 标记并在任务状态记录，最终输出尾部仍进入步骤结果。日志裁剪与模型上下文裁剪是两件独立的事。

设计来源与本轮取舍见 [开源 agent 对照](agent-comparison.md)。
