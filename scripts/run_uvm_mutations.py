"""Check bounded SV and UVM behavior faults in normally executed fixtures."""
from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path

from fpga_agent.evidence import sha
from fpga_agent.sv_conformance import conformance_valid, default_suite, load_contract, run_conformance, suite_inputs


MUTATIONS = [
    ('UVM-COMPONENT','uvm_component.sv','leaf = new("leaf",branch);','leaf = new("leaf",this);'),
    ('UVM-FACTORY','uvm_factory.sv','base_item::type_id::set_type_override(replacement_item::get_type());',';'),
    ('UVM-CONFIG','uvm_config.sv','"right","limit",29','"elsewhere","limit",29'),
    ('UVM-CONFIG-PRECEDENCE','uvm_config_precedence.sv','"lane_*.sink","limit",23',
     '"lane_a.sink","limit",23','config-narrow-scope'),
    ('UVM-CONFIG-PRECEDENCE','uvm_config_precedence.sv','set(this,"sink","limit",91);',
     'set(get_parent(),{get_name(),".sink"},"limit",91);','config-build-context'),
    ('UVM-CONFIG-PRECEDENCE','uvm_config_precedence.sv',
     'uvm_config_db#(int)::set(this,"lane_a.sink","limit",17);\n'
     '        uvm_config_db#(int)::set(this,"lane_*.sink","limit",23);',
     'uvm_config_db#(int)::set(this,"lane_*.sink","limit",23);\n'
     '        uvm_config_db#(int)::set(this,"lane_a.sink","limit",17);','config-build-tie'),
    ('UVM-CONFIG-PRECEDENCE','uvm_config_precedence.sv','set(lane_a.sink,"","limit",61);',
     'set(lane_b.sink,"","limit",61);','config-runtime-scope'),
    ('UVM-ANALYSIS','uvm_analysis.sv','ap.connect(second.analysis_export);',';'),
    ('UVM-SEQUENCE','uvm_sequence.sv','item.value=5*i+3;','item.value=5*i+4;'),
    ('UVM-ARBITRATION','uvm_arbitration.sv','UVM_SEQ_ARB_STRICT_FIFO','UVM_SEQ_ARB_FIFO','arbitration-fifo'),
    ('UVM-ARBITRATION','uvm_arbitration.sv','get_response(response,transaction);','get_response(response);','response-no-filter'),
    ('UVM-ARBITRATION','uvm_arbitration.sv','response.set_id_info(accepted[index]);',
     'response.set_id_info(accepted[index]);\n'
     '            foreach (accepted[other])\n'
     '                if (accepted[other].owner == 1 + accepted[index].owner % 3)\n'
     '                    response.set_sequence_id(accepted[other].get_sequence_id());','response-wrong-sequence'),
    ('UVM-ARBITRATION','uvm_arbitration.sv','3*accepted[index].value+7','3*accepted[index].value+8','response-wrong-value'),
    ('UVM-LOCK-GRAB','uvm_lock_grab.sv','                grab();','                lock();','grab-queues-as-lock'),
    ('UVM-LOCK-GRAB','uvm_lock_grab.sv','                lock();\n                acquired=has_lock();\n                #6;',
     '                acquired=has_lock();\n                #6;','lock-missing-owner'),
    ('UVM-LOCK-GRAB','uvm_lock_grab.sv','                send_value(11);\n                #3;',
     '                send_value(11);\n                unlock();\n                #3;','lock-early-release'),
    ('UVM-LOCK-GRAB','uvm_lock_grab.sv','                ungrab();\n                released=!has_lock();',
     '                released=!has_lock();\n                #2;\n                ungrab();','grab-late-release'),
    ('UVM-STOP-RESTART','uvm_stop_restart.sv','        super.clear_response_queue();',
     '        if (!restart) super.clear_response_queue();','restart-retains-old-response'),
    ('UVM-STOP-RESTART','uvm_stop_restart.sv','                seqr.stop_sequences();',
     '                driver.complete(112);\n                #1;\n                seqr.stop_sequences();','stop-completes-cancelled-request'),
    ('UVM-RTL-RESET','uvm_rtl_reset.sv','@(posedge clk or negedge reset_n)',
     '@(posedge clk)','rtl-reset-synchronous-only'),
    ('UVM-RTL-RESET','uvm_rtl_reset.sv','            response_data<=0;',
     "            response_data<=8'hff;",'rtl-reset-wrong-data'),
    ('UVM-RTL-RESET','uvm_rtl_reset.sv','                    seqr.stop_sequences();',
     '                    complete(bus.response_data);\n                    seqr.stop_sequences();','rtl-reset-false-completion'),
    ('UVM-REPORT','uvm_report.sv','`uvm_info("COUNT_INFO","second",UVM_NONE)',';'),
    ('UVM-FATAL','uvm_fatal.sv','`uvm_fatal("RSI_EXPECTED_FATAL","expected fatal termination")',
     '`uvm_warning("RSI_EXPECTED_FATAL","expected fatal termination")'),
    ('SV-RANDOMIZE','randomize.sv',"right == left + 4'd2;","right == left + 4'd3;",'randomize-wrong-relation'),
    ('SV-RANDOMIZE','randomize.sv','left inside {4,8};','left == 6;','randomize-wrong-inline'),
    ('SV-RANDOMIZE','randomize.sv','pair.left.rand_mode(0);','pair.left.rand_mode(1);','randomize-unfrozen-field'),
    ('SV-RANDOMIZE','randomize.sv','left == 1;','left == 3;','randomize-accepts-unsat'),
    ('UVM-RANDOMIZED-ITEM','uvm_randomized_item.sv','if (solved==1) begin',
     'if (solved==1 || trial==2) begin','randomized-item-forwards-unsat'),
    ('UVM-RANDOMIZED-ITEM','uvm_randomized_item.sv','observed.value=req.value;',
     'observed.value=req.value ^ 4\'d1;','randomized-item-corrupts-payload'),
    ('UVM-RANDOMIZED-ITEM','uvm_randomized_item.sv','observed.transaction_id=req.get_transaction_id();',
     'observed.transaction_id=req.get_transaction_id()+1;','randomized-item-changes-id'),
    ('UVM-RANDOMIZED-ITEM','uvm_randomized_item.sv','ap.write(observed);',
     'if (observed.transaction_id!=41) ap.write(observed);','randomized-item-drops-publication'),
    ('UVM-RTL-FIFO','rtl_fifo/fifo_two.sv','storage[write_index]<=in_word;',
     "storage[write_index]<=in_word ^ 16'h0001;",'fifo-corrupts-data'),
    ('UVM-RTL-FIFO','rtl_fifo/fifo_two.sv','if (pop) read_index<=~read_index;',
     'if (out_valid) read_index<=~read_index;','fifo-advances-while-stalled'),
    ('UVM-RTL-FIFO','rtl_fifo/fifo_two.sv',"                default: ;",
     "                2'b11: used<=used-1'b1;\n                default: ;",'fifo-simultaneous-loses-entry'),
    ('UVM-RTL-FIFO','uvm_rtl_fifo.sv','ap.write(sample);',
     'ap.write(sample);\n                    ap.write(sample);','fifo-duplicates-publication'),
    ('UVM-RTL-FIFO','uvm_rtl_fifo.sv','ap.write(sample);',
     "if (sample.word.id!=8'd64) ap.write(sample);",'fifo-drops-publication'),
    ('UVM-FIFO-SYNC-RESET','rtl_fifo/fifo_two.sv','always_ff @(posedge clk)',
     'always_ff @(posedge clk or negedge reset_n)','fifo-reset-clears-too-early'),
    ('UVM-FIFO-SYNC-RESET','rtl_fifo/fifo_two.sv','write_index<=0; used<=0;',
     "write_index<=0; if (used!==2) used<=0;",'fifo-reset-retains-full-count'),
    ('UVM-FIFO-SYNC-RESET','uvm_fifo_sync_reset.sv','seqr.stop_sequences();',
     'accepted=1;','fifo-reset-completes-cancelled-request'),
    ('UVM-FIFO-SYNC-RESET','uvm_fifo_sync_reset.sv','bus.reset_n && bus.out_valid && bus.out_ready',
     'bus.out_valid && bus.out_ready','fifo-reset-publishes-stale-word'),
    ('UVM-FIFO-REPEAT-RESET','uvm_fifo_repeat_reset.sv','if (exclusive) lock();',
     'if (exclusive && first_value==10) lock();','fifo-repeat-second-owner-unlocked'),
    ('UVM-FIFO-REPEAT-RESET','uvm_fifo_repeat_reset.sv','seqr.stop_sequences();',
     'if (bus.cycle==17) #2;\n                seqr.stop_sequences();','fifo-repeat-second-cancel-late'),
    ('UVM-FIFO-REPEAT-RESET','uvm_fifo_repeat_reset.sv','bus.reset_n && bus.out_valid && bus.out_ready',
     '(bus.reset_n || bus.cycle==17) && bus.out_valid && bus.out_ready','fifo-repeat-second-pop-leaks'),
    ('SV-FIFO-RESET-PRIORITY','rtl_fifo/fifo_two.sv','if (!reset_n) begin',
     'if (!reset_n && !(used==0 && push)) begin','fifo-empty-push-overrides-reset'),
    ('SV-FIFO-RESET-PRIORITY','rtl_fifo/fifo_two.sv','if (!reset_n) begin',
     'if (!reset_n && !(push && pop)) begin','fifo-simultaneous-overrides-reset'),
    ('SV-FIFO-RESET-PRIORITY','rtl_fifo/fifo_two.sv','if (!reset_n) begin',
     'if (!reset_n && !(used==2 && pop)) begin','fifo-full-pop-overrides-reset'),
    ('SV-FIFO-RESET-PRIORITY','rtl_fifo/fifo_two.sv','wire push=in_valid && in_ready, pop=out_valid && out_ready;',
     "wire pop=out_valid && out_ready;\n    wire push=in_valid && (in_ready || pop);",'fifo-full-allows-same-edge-write'),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    baseline = args.baseline_report.resolve()
    valid, reason = conformance_valid(baseline)
    data = json.loads(baseline.read_text(encoding='utf-8'))
    if not valid or data['uvm_fixture_profile_status'] != 'pass':
        parser.error('A current passing full bounded UVM fixture profile is required: '+reason)
    targets = {case for case, *_ in MUTATIONS}
    if not targets <= {row['id'] for row in data['cases'] if row['status'] == 'pass'}:
        parser.error('The baseline must also pass every SV mutation target')
    original = suite_inputs(default_suite())
    assert original == data['inputs']
    output = args.output.resolve()
    run = output.parent/'uvm-mutations'/uuid.uuid4().hex
    run.mkdir(parents=True)
    rows = []
    for case, filename, before, after, *labels in MUTATIONS:
        mutation = labels[0] if labels else case
        suite = run/mutation/'suite'
        shutil.copytree(default_suite(), suite)
        fixture = suite/filename if '/' in filename else suite/'cases'/filename
        content = fixture.read_text(encoding='utf-8')
        if content.count(before) != 1:
            raise ValueError('Mutation anchor changed: '+case)
        fixture.write_text(content.replace(before, after), encoding='utf-8')
        path = run/mutation/'result.json'
        report = run_conformance(path,suite=suite,case_ids=[case],backend=data['backend'],timeout=120)
        evidence_valid, evidence_reason = conformance_valid(path,suite=suite)
        observation = json.loads((path.parent/report['cases'][0]['observation']).read_text(encoding='utf-8'))
        executed = (observation.get('slang',{}).get('status') == 'accepted'
                    and observation.get('compile',{}).get('returncode') == 0
                    and observation.get('build',{'returncode':0}).get('returncode') == 0
                    and observation.get('runtime',{}).get('returncode') == 0
                    and not observation.get('runtime',{}).get('execution_error'))
        case_result = report['cases'][0]
        rejected_behavior = case_result['status'] == 'fail'
        if case in {'UVM-ARBITRATION', 'UVM-CONFIG-PRECEDENCE', 'UVM-LOCK-GRAB', 'UVM-STOP-RESTART',
                    'UVM-RTL-RESET', 'SV-RANDOMIZE', 'UVM-RANDOMIZED-ITEM', 'UVM-RTL-FIFO',
                    'UVM-FIFO-SYNC-RESET', 'UVM-FIFO-REPEAT-RESET', 'SV-FIFO-RESET-PRIORITY'}:
            transcript = observation.get('runtime',{}).get('output','')
            completed = any(line.strip().removeprefix('# ').lstrip() == f'DONE {case}'
                            for line in transcript.splitlines())
            rejected_behavior = (rejected_behavior and completed
                                 and case_result['reason'].startswith('Observation mismatch:'))
        caught = evidence_valid and executed and rejected_behavior
        rows.append({'case':case,'mutation':mutation,'caught':caught,'executed_normally':executed,
                     'status':report['status'],'evidence_valid':evidence_valid,
                     'reason':report['cases'][0]['reason'],'validation_reason':evidence_reason,
                     'report':path.relative_to(output.parent).as_posix(),'sha256':sha(path),
                     'fixture_sha256':sha(fixture),'oracle_sha256':report['inputs']['@sv_reference.py']})
        print(f'{mutation}: runtime fault detected={caught}',flush=True)
    stable = suite_inputs(default_suite()) == original
    result = {'schema_version':2,'status':'pass' if stable and all(r['caught'] for r in rows) else 'fail',
              'scope':'Bounded SV and UVM behavioral mutations',
              'baseline_report':str(baseline),'baseline_sha256':sha(baseline),'backend':data['backend'],
              'unmodified_inputs_stable':stable,'runner_sha256':sha(Path(__file__)), 'mutations':rows}
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('Evidence: '+str(output),flush=True)
    return 0 if result['status']=='pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
