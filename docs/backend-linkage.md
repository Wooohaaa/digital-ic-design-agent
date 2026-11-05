# OpenCode、Claude Code 与 Codex 联动

本机 OpenCode 1.18.21 已安装；本轮使用官方 npm 包安装 Claude Code 2.1.318。新版 npm 包以 `bin/claude.exe` 提供原生程序，IC Forge 现在能从 Windows npm 包装器解析它，也兼容旧版 `cli.js`，不通过 shell 拼接模型提示。

## 执行者模型

```text
/provider codex
/model native
/mode orchestrate
/agents 3 codex,claude,opencode
/agent-model opencode opencode/deepseek-v4.1-flash-free
/agent-model claude x-deepseek-v4.1-flash
/agents
```

主管使用 Codex 已有登录；OpenCode 执行者选其原生免费 DeepSeek 模型，保留用户原有的 OpenCode 全局配置。Claude Code 是执行工具，底层模型另行配置：按用户要求使用原网关的 `x-deepseek-v4.1-flash`，不能表述为 Claude 模型已登录。

上述 Claude 配置只适用于用户已授权的兼容网关。`x-deepseek-v4.1-flash` 通过网关的 Anthropic Messages 接口调用；Anthropic 不为经第三方网关使用非 Claude 模型提供支持，后续协议变化仍需实测。普通 Claude 用户应采用原生账号/官方 API，并使用 `/agent-model claude native`。

## 可选 Claude 网关兼容层

当前 Claude Code 的实际请求会在消息列表中附加 `role=system` 的运行环境信息。部分 Anthropic 兼容网关仅接受顶层 `system` 字段，返回 `400 非法 role: system`。IC Forge 提供显式启用的转换：保持系统指令的内容、顺序和系统优先级，将这些文本及已有 system 文本合并到标准顶层字段。用户/助手消息与工具定义保持原样；不支持的系统内容类型直接报错。

在用户主目录创建 `.icforge/claude-gateway.json`，不要放入代码仓库：

```json
{
  "base_url": "https://your-gateway.example",
  "api_key": "你的网关凭据",
  "system_string": true
}
```

也可通过 `ICFORGE_EXECUTOR_ANTHROPIC_AUTH_TOKEN` 与 `ICFORGE_EXECUTOR_ANTHROPIC_BASE_URL` 覆盖上游认证和地址。未创建上述配置或未启用选项时，不启动兼容层。

每个 Claude 分工启动独立的临时回环代理，监听 `127.0.0.1` 随机端口，要求随机本地令牌；向上游使用 HTTPS，并支持流式响应和 SDK 查询参数。代理只接受 Messages / token-count 请求。任务结束、失败或暂停时关闭，密钥不写入任务检查点。该功能作用于 IC Forge 启动的 Claude Code 分工，不会启动常驻服务，也不会为独立运行的 `claude` 命令配置账号。

任务内的临时设置文件只含回环地址及该分工的本地随机令牌，任务结束后令牌失效；上游凭据保留在用户配置目录。模型、额度和服务端协议是否可用，以真实调用结果为准。

## 实测范围

连接测试已分别通过 OpenCode 原生免费模型与 Claude Code 经兼容层的 DeepSeek 真实响应。混合任务 `34e94cbcf812` 已完成并通过宿主验收：

| 执行者 | 模型 | 任务与实际结果 |
| --- | --- | --- |
| OpenCode 1.18.21 | opencode/deepseek-v4.1-flash-free | 实现 calc/add.py，通过范围检查 |
| Claude Code 2.1.318 | x-deepseek-v4.1-flash | 真实调用 Read / Write，实现 calc/multiply.py |
| Codex 0.153.4 | 原生配置 | 等待两个实现完成后只读审查 |
| IC Forge 宿主 | 固定 unittest | 两项测试通过，独立重跑也通过 |

固定测试与初始化文件的 SHA256 和任务开始时一致。机器可读证据见 [混合任务运行结果](backend-linkage-live-result.json)。本次经过兼容修复、用户指定切换为 DeepSeek 后恢复任务，已完成的 OpenCode 节点被复用；不是所有最终模型从零同时开始的性能比较。147 项自动回归通过，覆盖原生程序识别、模型配置、协议规范化、回环认证、流式转发、查询参数及资源关闭。该实验验证跨 CLI 协调机制，不代表复杂 IC 工程或多日运行已验收。

接口依据：[Claude Code 安装](https://code.claude.com/docs/en/setup)、[网关配置](https://code.claude.com/docs/en/llm-gateway-connect)、[网关兼容范围](https://code.claude.com/docs/en/llm-gateway)、[OpenCode 模型](https://opencode.ai/docs/zen/)。OpenCode 文档明确免费层聊天模型需通过 OpenCode 使用；本实现使用 OpenCode CLI，没有将免费接口转给其他客户端。
