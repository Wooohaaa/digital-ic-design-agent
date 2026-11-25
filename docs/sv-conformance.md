# SV/UVM 能力矩阵与独立语义参考

日期：2026-11-17，第四轮。契约文件：[contract.json](../fpga_agent/templates/conformance/contract.json)。
本页保留第四轮初始观测；“无第二运行仿真器”已被第五轮 [第二后端与差异证据](sv-backends.md) 取代。Icarus 失败结论保留，ModelSim 的通过另行记录，不能合并为全语言支持。
后续版本见 [v0.2 UVM 夹具](uvm-profile.md) 与 [Verilator 开源运行后端](verilator-runtime.md)。
这是 `rsi-sv-uvm-v0.1` 有界夹具能力清单，不是 SV 全语言认证、完整 UVM 实现或自研仿真器。

## 已固定的契约

- 长期 SV 语义目标为 IEEE 1800-2023；首轮夹具使用其中较早版本已有的子集。Icarus 以 `-g2012` 执行，slang 以 1800-2023 解析/elaborate；不把接受一个源文件等同于支持整部标准。
- slang 前端通过可选 `pyslang==12.1.0` 绑定，版本和传递依赖锁在 `uv.lock`。它提供独立解析、类型检查和 elaboration，**不作为第二个仿真器**。[slang 项目说明](https://github.com/MikePopoloski/slang)
- UVM 最小目标基于 IEEE 1800.2-2020 和官方 Accellera 2020.3.1，设置 `UVM_NO_DPI`。固定 commit 为 `78c06547a2a0a29b3dc9dcafae62b75b2ff61544`；174 个源码/许可文件的规范化哈希见 [uvm.lock.json](../fpga_agent/templates/conformance/uvm.lock.json)。这是固定基线，不随 latest 自动改变。[官方版本](https://github.com/accellera-official/uvm-core/releases/tag/2020.3.1)
- 官方库只放 `.tools/` 缓存，未复制成项目自研 UVM。探针快照保留 Apache-2.0 LICENSE、NOTICE 和偏差说明；缺失、额外或变更源码会阻止该探针。

## 当前执行路径

1. 读取版本化契约、feature ID、case ID、界限、必选/探针角色；保存源码/契约/checker 指纹并建立独立快照。
2. slang 在隔离子进程中完成解析、类型检查与 elaboration。有效的文件若被其中一个前端拒绝，只记录差距；编译器崩溃、超时不能当作“正确拒绝”。
3. Icarus 编译并用 vvp 运行。共享 64 个固定边界/随机输入向量，独立 Python 模块 `sv_reference.py` 根据整数运算、四态真值表、状态递推和存储更新规则计算期望值；它不解析、转译或执行被测 SV。
4. 逐项比对有序 `OBS case-id ...` 序列及唯一 DONE。缺行、重复、错 ID、顺序错误、错误值、runtime error 或只打印 PASS 都不能通过；编译输出也不能替代运行观测。
5. `conformance_valid` 重验契约、快照、官方库、输入/checker、原始日志、独立参考结果、case 库存和 feature 状态。有效的失败报告表示失败证据可复核，不表示被测功能通过。

记录的能力状态包括 `fixture_verified`（仅该界限）、`frontend_only`（仅静态接受）、`not_verified`、`blocked` 和 `planned`。
完整 SV/UVM 与 UVM 最小子集的完成标志在 v0.1 均为 false；本机没有执行第二个仿真器。

## 本机观测与未关闭项

基础门槛 8 个 case 通过，覆盖 9 个有界 feature：8 位整数运算/移位/比较、packed struct/enum、package、automatic function、参数/generate、NBA 采样、unpacked byte array、标量四态逻辑和独立语法错误拒绝。
其中整数、类型/包/生成/数组使用 64 组输入；NBA 为 32 个上升沿；四态为全部 16 对 0/1/x/z 输入。范围有限，不能推广到整个相应语言特性。

完整 11-case 探针矩阵保留 **fail**，基础门槛仍单独标为 pass：

| 探针 | slang | 本机 Icarus 13.0 观测 | 能力结论 |
| --- | --- | --- | --- |
| SV-CLASS | 接受 | base 对象返回 12；derived 直接调用返回 18；同一 derived 对象经 base handle 调用返回 9，独立参考期望 18 | 运行语义不一致，不能声称该虚调用子集已验证 |
| SV-INTERFACE | 接受 | interface/modport 模块端口编译拒绝 | frontend_only，未验证运行 |
| UVM-PHASE | 官方完整库及测试接受 | 官方库编译拒绝，未运行 phase/objection 测试 | phase/objection 仅 frontend_only，不能声称 UVM runtime 支持 |

Icarus 的 SV 支持边界应以实际版本和测试为准，项目本身也明确说明支持仍在推进。[Icarus 源码与说明](https://github.com/steveicarus/iverilog)
上述类结果是不一致的可复现反例；目前没有通过修改期望值、跳过观测或伪造 UVM 层来消除它。

UVM component hierarchy、factory override、config_db、analysis fanout/scoreboard、sequence/sequencer/driver 和 reporting 的最小契约已列入清单，运行夹具尚未实现，保持 planned。整套最小 UVM 尚未完成。

## 运行与复现

```powershell
python -m pip install -e '.[conformance]'
# 也可 uv sync --locked --all-extras；将 iverilog/vvp 加入 PATH
python -m fpga_agent sv-check --required-only
python -m fpga_agent sv-check
python -m fpga_agent sv-check --case SV-CLASS
```

默认报告为 `reports/sv-conformance.json`，详细产物在相邻 `sv-runs/<id>/`。可用 `--output`、`--timeout` 和 `--uvm-root` 指定输出/预算/官方源码；`--case` 与 `--required-only` 不能混用。
首次获得固定 UVM 缓存可执行：

```powershell
git clone --branch 2020.3.1 --depth 1 https://github.com/accellera-official/uvm-core.git .tools/uvm-core-2020.3.1
```

runner 不会自动联网下载；缓存必须通过已固定的完整文件清单和哈希检查。
退出码 0 表示选定的有界检查通过；1 为语义失败/工具异常；2 为差距或缺工具。`--required-only` 的绿色结果只对上述基础子集有效，完整探针的非零状态须保留。
Agent/终端新增 typed action `/sv-check`；原 CPU/LEC 门禁不因这份矩阵被升级为完整 SV/UVM 认证。CI 仅加入明确命名的基础子集检查，远端尚未执行。

本轮报告位于根工作区 `validation/2026-11-17-sv-capabilities.json`（完整失败边界）和 `2026-11-17-sv-baseline.json`（基础通过）；二者均可重验。wheel 交付包含契约、11 个夹具、锁文件和检查器，不包含外部 UVM 工具缓存。
最终软件回归 258 passed / 1 skipped，127.90 秒；依赖锁离线检查、wheel 构建及内容检查通过。跳过项为 Windows symlink 权限；汇总及指纹见根工作区 `validation/2026-11-17-sv-summary.json`。软件回归通过不改变完整能力矩阵的 fail 状态。

后续先保留/扩大类虚调用反例，接入可运行这些构造的独立仿真后端，再逐项实现 UVM 最小目标的运行夹具；不能用静态成功填补运行语义空白。
