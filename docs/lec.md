# 组合逻辑等价性检查（LEC）

`icforge lec` 验证工程顶层的组合 RTL 与网表。它会综合出通用门级网表，也可以检查工程内提供的候选网表。Python 依赖不包含 Yosys；请选择 PATH 中的 `yosys` 或 `yowasp-yosys`。本地验证使用 YoWASP Yosys `0.71.0.0.post1285`。

```sh
icforge lec examples/lec/adder
icforge lec path/to/project --netlist rtl/candidate.v
python -m pytest tests/test_lec.py -q
```

输入来自 `project.json` 的 `top_module` 与 `rtl_sources`。Yosys 检查两边端口一致，并构造比较所有输出的 miter；SAT 检查是否存在使两边输出不同的二值输入。发现差异时，`build/lec/<run>/counterexample.json` 保存反例。报告写入 `reports/lec.json`，绑定输入快照、工具版本、脚本、日志和通用网表。退出码 0 表示通过，1 表示失败，2 表示 Yosys 未就绪。

当前范围为 Yosys 支持的可综合组合 RTL 和通用门。映射后仍有状态单元、工艺库黑盒、X/Z 信号或没有输出的设计会被拒绝。RTL 和网表共享 Yosys 语义，证据不覆盖时序、完整四态仿真或物理签核。当前通过独立命令执行，不改变既有工程门禁。
