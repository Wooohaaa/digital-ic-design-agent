# 厂商中立综合（RTL→netlist）

`icforge synth` 用 Yosys 把工程 RTL 降到**技术无关的门级网表**，不依赖任何厂商库、布局布线工具或许可证，因此在任何装好 Yosys 的 Windows/Linux 主机上都能运行，不需要安路 TD。它是实现层里 vendor-neutral 的证据门禁，回答两个问题：RTL 能不能综合？需要多少通用单元/触发器资源？

它与 `icforge lec`（等价证明）和安路 `icforge td`（厂商实现）是互补的：`td` 是 HX1P35A 专属的布局布线与位流路径，`synth` 只证明「可综合」并给出通用网表，两者都不互相替代。

## 后端与安装

后端解析顺序与 LEC/formal 相同：`ICFORGE_YOSYS` 覆盖 → `PATH` 上的原生 `yosys` → 工程自带 `.tools/yosys/bin` → 当前 Python 环境里的 `yowasp-yosys`。

```powershell
pip install -e ".[formal]"
icforge synth examples/agent_validation/sync_fifo
```

缺少后端时写 `status: blocked` 报告并返回退出码 2，绝不假装成功。

## 流程

```text
read_verilog -sv <sources>
hierarchy -top <top>
proc; opt; memory; opt          # 展开进程、合并推断存储
chformal -remove                # 去掉只服务形式验证的 $check/$assume 单元
flatten                         # 默认展平为单一网表（--no-flatten 保留层次）
techmap; opt                    # 降到技术无关门单元
tee -q -o <top>_stat.json stat -json
write_verilog -noattr <top>_synth.v
```

- `techmap` 使用 Yosys 内置 simplemap 规则得到 `$_AND_`/`$_OR_`/`$_XOR_`/`$_MUX_`/`$_DFF*_` 等通用单元。**刻意不用 ABC**：可移植 WASM 构建在异步复位设计上会不稳定。
- `memory` 是必需的：网表和后续消费方都无法直接处理未展开的 `$memwr_v2`/`$memrd` 单元。
- `write_verilog` 会把触发器单元输出为行为式 `always` 块、门单元输出为实例，这是 Yosys 的正常行为；网表仍是技术无关的。

## 产物

| 文件 | 内容 |
|---|---|
| `build/synth/<top>_synth.v` | 技术无关门级网表（含 SHA-256） |
| `build/synth/<top>_stat.json` | `stat -json` 原始单元统计 |
| `build/synth/synth.ys` | 实际执行的 Yosys 脚本 |
| `reports/synth.json` | 结构化证据：状态、顶层、单元总数、触发器数、逐类型计数、网表路径与哈希、工具版本、输入哈希、耗时 |
| `reports/synth.log` | 原始 Yosys 输出 |

## 诚实性约定

- 产物是**技术无关网表**，不是厂商映射、布局布线结果或位流。
- `cells`/`flops` 是从通用网表得到的资源估算，**不是面积、时序或功耗**，也不是厂商资源报告。
- 报告绑定所有声明源文件（含递归 `include`）与配置的哈希；改动任一源文件即失效，旧网表不会读作当前证据。
- 没有 Yosys 时为 `blocked`。综合失败/超时/未产出网表时为 `error`，不会记 `pass`。

## Manifest 配置

```json
{
  "top_module": "sync_fifo",
  "rtl_sources": ["rtl/sync_fifo.v"],
  "synth": {
    "top": "sync_fifo",
    "flatten": true,
    "sources": ["rtl/sync_fifo.v"]
  }
}
```

命令行参数优先于 manifest。`sources` 缺省回退到 `rtl_sources`；`flatten` 缺省为 `true`。

## 工程审查门禁

`icforge review` 把报告映射为可选门禁 `SYNTH-001`（`required: false`，owner 为实现）：

- 当前输入上综合成功且网表哈希一致 → `pass`；
- 没有报告 → `not_run`；
- 缺 Yosys → `blocked`；
- 综合出错或网表被改动 → `blocked`。

它不阻断 `artifact_ready`，但随工程审查报告和交付包归档。

## 退出码

`0` 通过；`2` 阻塞或出错（没有 `fail` 状态——综合要么产出网表，要么报错）。

## 参考来源

- [Yosys 文档](https://yosyshq.readthedocs.io/)：`techmap`、`memory`、`chformal`、`stat -json`、`write_verilog` 命令语义。
- [YosysHQ/yowasp](https://github.com/YosysHQ/yowasp)：可移植 WASM 后端。
