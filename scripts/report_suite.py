"""Summarize existing content-validated simulation/implementation evidence."""
from pathlib import Path
import json
import sys
import zipfile
from datetime import datetime,timezone,timedelta

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fpga_agent.evidence import simulation_valid,implementation_valid,sha

results=[]
labels={'rv32_single':'单周期','rv32_multi':'多周期','rv32_pipeline':'两级流水线'}
for name in ('rv32_single','rv32_multi','rv32_pipeline'):
    project=ROOT/'benchmarks'/name
    manifest=json.loads((project/'project.json').read_text(encoding='utf-8'))
    sim=json.loads((project/'reports/simulation.json').read_text(encoding='utf-8'))
    td=json.loads((project/'reports/td.json').read_text(encoding='utf-8'))
    sv,sr=simulation_valid(project,manifest,sim);tv,tr=implementation_valid(project,manifest,td)
    archive=project/'deliverables'/f'{name}-delivery.zip'
    with zipfile.ZipFile(archive) as z:
        zip_ok=z.testzip() is None and sum(p.endswith('.bit') for p in z.namelist())==1
    results.append({'project':name,'architecture':labels[name],'model':manifest.get('model_backend'),
        'simulation':'pass' if sv else 'fail','simulation_reason':sr,'implementation':'pass' if tv else 'fail',
        'implementation_reason':tr,'cases':sim['case_count'],'random_seeds':sim['seeds'],
        'retired_instructions':sum(c.get('retired',0) for c in sim['cases']),
        'simulation_cycles':sum(c.get('cycles',0) for c in sim['cases']),
        'coverage':sim['instruction_coverage'],'timing':td['timing_summary'],'resources':td['resource_summary'],
        'bitstreams':td['bitstream_hashes'],'delivery':str(archive.relative_to(ROOT)).replace('\\','/'),
        'delivery_sha256':sha(archive),'archive_valid':zip_ok,'reference_hashes':sim['trusted_harness_hashes']})
report={'created_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'target':'PH1P35MDG324 / HX1P35A','clock_mhz':50,'results':results}
(ROOT/'benchmarks/suite-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['# RISC-V CPU 实测结果','',f"生成时间：{report['created_at']}。目标 PH1P35MDG324 / HX1P35A，TD V6.2.168116，50 MHz。",'',
 '| 结构 | 独立程序 | 提交指令 | 仿真周期 | LUT6 | 寄存器 | Setup WNS ns | Hold WNS ns | 交付 |',
 '|---|---:|---:|---:|---:|---:|---:|---:|---|']
for item in results:
    t=item['timing'];r=item['resources']
    lines.append(f"| {item['architecture']} | {item['cases']} | {item['retired_instructions']} | {item['simulation_cycles']} | {r['lut6']['used']} | {r['reg']['used']} | {t['setup_wns_ns']:.3f} | {t['hold_wns_ns']:.3f} | [ZIP](../{item['delivery']}) |")
lines += ['', '三份候选均由 gpt-6-luna 在当前会话生成，随后以 exchange 文件桥接作为 agent 的临时模型后端。每份包含 50 个定向程序和 64 个确定性随机程序，另验证用于综合的 ROM/RAM SoC 签名。所有最终 setup/hold TNS 均为 0。资源统计包含板级 SoC 测试封装，不是纯 CPU 核面积。', '',
 '仿真周期是整个回归集合的总数，包含终止/异常检测和流水线填充/冲刷；不能直接作为 CoreMark 或系统性能指标。', '',
 '详细状态、覆盖点、位流和交付包 SHA-256 见 [suite-report.json](../benchmarks/suite-report.json)。单工程原始报告位于各 `reports/simulation.json` 和 `reports/td.json`。', '',
 '多周期核的原始 SRAI 失败反例保存在 `benchmarks/failures/rv32_multi_initial`，修复记录见 [repair_001.json](../benchmarks/rv32_multi/generation/repair_001.json)。', '',
 '本轮没有下载开发板，尚未跑 Spike/官方架构测试或形式证明。执行环境与重现步骤见 [riscv-flow.md](riscv-flow.md)。']
(ROOT/'docs/riscv-results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
for result in results:print(result['project'],result['simulation'],result['implementation'],result['cases'],result['timing'])
if not all(r['simulation']=='pass' and r['implementation']=='pass' and r['archive_valid'] for r in results):raise SystemExit(1)
