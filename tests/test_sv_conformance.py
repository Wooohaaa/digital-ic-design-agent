from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from fpga_agent.sv_conformance import (classify, conformance_valid, default_suite,
                                      load_contract, run_conformance, trace_matches, verify_uvm)
from fpga_agent.sv_reference import expected, vectors


def transcript(case):
    return '\n'.join('OBS '+case+' '+' '.join(row) for row in expected(case))+'\nDONE '+case+'\n'


def test_independent_integer_and_four_state_reference_edges():
    assert len(vectors()) == 64 and vectors() == vectors()
    rows = expected('SV-INT')
    assert rows[2][1] == '0'  # 255+1 wraps to eight bits.
    assert rows[3][2:] == ['240', '1', '0', '255']  # signed -128 shifted by 3.
    truth = {(r[0],r[1]):r[2:] for r in expected('SV-4STATE')}
    assert truth[('0','2')] == ['0','x','x','0']
    assert truth[('1','3')] == ['x','1','x','0']
    assert truth[('3','3')] == ['x','x','x','1']


def test_randomization_oracle_accepts_different_solutions_but_rejects_illegal_traces():
    case = 'SV-RANDOMIZE'
    rows = [f'base {index} 1 2 4' for index in range(8)] + [
        'inline 0 1 4 6', 'frozen 0 1 5 7', 'frozen_bad 0 0 12 15',
        'thawed 0 1 3 5', 'unsat 0 0 0 15',
    ]

    def output(observations):
        return ''.join(f'OBS {case} {row}\n' for row in observations) + f'DONE {case}\n'

    assert trace_matches(case, output(rows))[0]
    alternative = [f'base {index} 1 9 11' for index in range(8)] + ['inline 0 1 8 10'] + rows[9:]
    assert trace_matches(case, output(alternative))[0]
    for index, wrong in (
        (0, 'base 0 1 x 4'), (8, 'inline 0 1 6 8'), (9, 'frozen 0 1 6 8'),
        (10, 'frozen_bad 0 0 12 14'), (12, 'unsat 0 1 0 15'),
    ):
        mutated = rows.copy()
        mutated[index] = wrong
        assert not trace_matches(case, output(mutated))[0]
    assert not trace_matches(case, output(rows[:-1]))[0]
    assert not trace_matches(case, output(rows).replace('DONE', 'SKIP'))[0]


def test_randomized_items_must_arrive_unchanged_and_only_on_success():
    case = 'UVM-RANDOMIZED-ITEM'
    rows = [
        'sequence 40 1 2 4', 'driver 40 2 4', 'scoreboard 40 2 4',
        'sequence 41 1 4 6', 'driver 41 4 6', 'scoreboard 41 4 6',
        'sequence 42 0 0 15',
        'sequence 43 1 7 9', 'driver 43 7 9', 'scoreboard 43 7 9',
    ]

    def output(observations):
        return (''.join(f'OBS {case} {row}\n' for row in observations)
                + f'DONE {case}\nUVM_ERROR : 0\nUVM_FATAL : 0\n')

    assert trace_matches(case, output(rows))[0]
    assert trace_matches(case, output([row.replace('4 6', '8 10') for row in rows]))[0]
    assert not trace_matches(case, output(rows[:3] + [row.replace('4 6', '6 8') for row in rows[3:6]] + rows[6:]))[0]
    for index, invalid in ((1, 'driver 40 3 5'), (2, 'scoreboard 41 2 4'), (6, 'sequence 42 0 0 14')):
        assert not trace_matches(case, output(rows[:index] + [invalid] + rows[index+1:]))[0]
    forwarded = ['driver 42 0 15', 'scoreboard 42 0 15']
    assert not trace_matches(case, output(rows[:7] + forwarded + rows[7:]))[0]
    assert not trace_matches(case, output(rows[:5] + rows[6:]))[0]
    assert not trace_matches(case, output(rows + [rows[-1]]))[0]
    assert not trace_matches(case, output(rows).replace('UVM_ERROR : 0', 'UVM_ERROR : 1'))[0]


def test_rtl_fifo_oracle_requires_backpressure_order_and_exact_publication():
    case = 'UVM-RTL-FIFO'

    def observations(first_key):
        payloads = {0: (0, 0), 60: (first_key, first_key+2), 61: (4, 6),
                    62: (0, 15), 63: (5, 7), 64: (3, 5), 65: (7, 9)}
        attempts = {0: [60], 2: [61], 4: [62, 63], 9: [64], 11: [65]}
        # Explicit waveform: input ID, input ready, output ID, sink ready.
        edges = [(0,1,0,0), (60,1,0,0), (0,1,60,0), (61,1,60,0),
                 (0,0,60,0), (63,0,60,0), (63,0,60,0), (63,0,60,1),
                 (63,1,61,1), (0,1,63,1), (64,1,0,0), (0,1,64,0),
                 (65,1,64,1), (0,1,65,1), (0,1,0,1), (0,1,0,1)]
        rows = []
        for cycle, (incoming, ir, outgoing, ready) in enumerate(edges):
            for ident in attempts.get(cycle, []):
                rows.append(('solution', ident, int(ident != 62), *payloads[ident]))
            rows.append(('edge', cycle, int(bool(incoming)), ir, incoming, *payloads[incoming],
                         int(bool(outgoing)), ready, outgoing, *payloads[outgoing]))
            if outgoing and ready:
                rows.append(('score', cycle, outgoing, *payloads[outgoing]))
        return rows + [('completed', 5)]

    def check(rows):
        output = ''.join(f'OBS {case} {" ".join(map(str, row))}\n' for row in rows)
        return trace_matches(case, output+f'DONE {case}\nUVM_ERROR : 0\nUVM_FATAL : 0\n')

    rows = observations(2)
    for variant in (rows, observations(9)):
        ok, reason = check(variant)
        assert ok and '3 blocked input edges, 6 blocked output edges and 2 simultaneous transfers' in reason
    stalled_input = next(i for i, row in enumerate(rows) if row[:2] == ('edge', 6))
    broken_hold = (*rows[stalled_input][:2], 0, *rows[stalled_input][3:])
    assert 'withdrew' in check(rows[:stalled_input]+[broken_hold]+rows[stalled_input+1:])[1]
    stalled_output = next(i for i, row in enumerate(rows) if row[:2] == ('edge', 4))
    wrong_head = (*rows[stalled_output][:-3], 61, 4, 6)
    assert 'queue head' in check(rows[:stalled_output]+[wrong_head]+rows[stalled_output+1:])[1]
    score = next(i for i, row in enumerate(rows) if row[0] == 'score')
    assert not check(rows[:score]+rows[score+1:])[0]
    assert not check(rows[:score]+[rows[score]]+rows[score:])[0]
    assert not check(rows[:-2]+rows[-1:])[0]


@pytest.mark.parametrize('output', ['PASS\n', '', 'OBS OTHER 0 1\nDONE SV-CLASS\n',
    transcript('SV-CLASS').replace('2 18', '2 9'), transcript('SV-CLASS')+'DONE SV-CLASS\n',
    transcript('SV-CLASS')+'FATAL: assertion failed\n', transcript('SV-CLASS')+'UVM_ERROR : 1\n'])
def test_missing_forged_foreign_duplicate_or_wrong_results_do_not_pass(output):
    assert not trace_matches('SV-CLASS', output)[0]


def test_exact_rows_and_zero_uvm_error_summary_are_accepted():
    assert trace_matches('SV-CLASS', transcript('SV-CLASS'))[0]
    assert trace_matches('UVM-PHASE', transcript('UVM-PHASE')+'UVM_ERROR : 0\nUVM_FATAL : 0\n')[0]


def test_modelsim_prefix_preserves_order_and_rejects_simulator_errors():
    output = '\n'.join('# '+line for line in transcript('SV-CLASS').splitlines())
    assert trace_matches('SV-CLASS', output)[0]
    assert not trace_matches('SV-CLASS', output+'\n# ** Error: failed assertion')[0]
    assert not trace_matches('SV-CLASS', output.replace('2 18', '2 9'))[0]
    assert not trace_matches('SV-CLASS', output+'\n# UVM_FATAL : 1')[0]


def fatal_transcript():
    return ('OBS UVM-FATAL 0 1\n'
            'UVM_FATAL @ 2: uvm_test_top [RSI_EXPECTED_FATAL] expected fatal termination\n'
            'UVM_ERROR : 0\nUVM_FATAL : 1\n')


@pytest.mark.parametrize('change', ['no_fatal','wrong_id','duplicate_fatal','missing_counts',
                                  'duplicate_counts','nonzero_errors','continued','fake_done'])
def test_expected_fatal_requires_specific_message_counts_and_no_continuation(change):
    output = fatal_transcript()
    assert trace_matches('UVM-FATAL',output)[0]
    if change == 'no_fatal': output = '\n'.join(line for line in output.splitlines() if 'RSI_EXPECTED_FATAL' not in line)
    elif change == 'wrong_id': output = output.replace('RSI_EXPECTED_FATAL','OTHER')
    elif change == 'duplicate_fatal': output += output.splitlines()[1]+'\n'
    elif change == 'missing_counts': output = output.replace('UVM_FATAL : 1','')
    elif change == 'duplicate_counts': output += 'UVM_FATAL : 1\n'
    elif change == 'nonzero_errors': output = output.replace('UVM_ERROR : 0','UVM_ERROR : 1')
    elif change == 'continued': output += 'FAIL UVM-FATAL returned after fatal\n'
    elif change == 'fake_done': output += 'DONE UVM-FATAL\n'
    assert not trace_matches('UVM-FATAL',output)[0]


def test_normal_uvm_checks_require_zero_error_fatal_summary():
    output = transcript('UVM-FACTORY')
    assert not trace_matches('UVM-FACTORY', output)[0]
    assert trace_matches('UVM-FACTORY', output+'UVM_ERROR : 0\nUVM_FATAL : 0\n')[0]
    assert not trace_matches('UVM-FACTORY', output+'UVM_ERROR : 0\nUVM_FATAL : 1\n')[0]


def test_expected_fatal_cannot_be_satisfied_by_crash_or_timeout():
    case = next(c for c in load_contract(default_suite())['cases'] if c['id']=='UVM-FATAL')
    observation = {'slang':{'status':'accepted'}, 'compile':{'returncode':0,'output':''},
                   'runtime':{'returncode':-6,'output':fatal_transcript()}}
    assert classify(case,observation)[0] == 'fail'
    observation['runtime'] = {'returncode':None,'output':fatal_transcript(),'execution_error':'timeout'}
    assert classify(case,observation)[0] == 'fail'


def test_compiler_observations_cannot_replace_runtime_observations():
    case = next(c for c in load_contract(default_suite())['cases'] if c['id']=='SV-INT')
    observation = {'slang':{'status':'accepted'},
                   'compile':{'returncode':0,'output':transcript('SV-INT')},
                   'runtime':{'returncode':0,'output':'PASS\n'}}
    assert classify(case,observation)[0] == 'fail'


@pytest.mark.parametrize('code,error', [(-6,None),(134,None),(124,None),(None,'timeout'),(0,None)])
def test_crash_timeout_or_acceptance_cannot_count_as_negative_syntax_success(code,error):
    case = next(c for c in load_contract(default_suite())['cases'] if c['kind']=='reject')
    compile_result = {'returncode':code,'output':'syntax error'}
    if error: compile_result['execution_error'] = error
    status,_ = classify(case,{'slang':{'status':'rejected','error_count':1},'compile':compile_result})
    assert status != 'pass'


def test_valid_syntax_rejection_needs_both_frontends():
    case = next(c for c in load_contract(default_suite())['cases'] if c['kind']=='reject')
    observation = {'slang':{'status':'rejected','error_count':1},'compile':{'returncode':1,'output':'syntax error'}}
    assert classify(case,observation)[0] == 'pass'
    observation['slang']['status'] = 'accepted'
    assert classify(case,observation)[0] == 'fail'


@pytest.mark.parametrize('backend', ['icarus', 'modelsim', 'verilator'])
def test_missing_tools_are_blocked_and_do_not_claim_complete_support(tmp_path, backend):
    path = tmp_path/'result.json'
    with patch('fpga_agent.sv_conformance.shutil.which', return_value=None):
        report = run_conformance(path,required_only=True,backend=backend)
    assert report['status'] == 'blocked'
    assert report['full_sv_uvm_complete'] is False
    assert report['uvm_minimum_complete'] is False
    assert all(row['status']=='blocked' for row in report['cases'])
    assert conformance_valid(path)[0]
    report['backend'] = 'modelsim' if backend == 'icarus' else 'icarus'
    path.write_text(json.dumps(report))
    assert not conformance_valid(path)[0]


def test_case_inventory_and_path_escape_rejected(tmp_path):
    suite = tmp_path/'suite'
    shutil.copytree(default_suite(),suite)
    data = json.loads((suite/'contract.json').read_text())
    data['cases'].append(data['cases'][0])
    (suite/'contract.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='duplicate'):
        load_contract(suite)
    with pytest.raises(ValueError):
        run_conformance(tmp_path/'result.json',case_ids=['UNKNOWN'])
    with pytest.raises(ValueError, match='backend'):
        run_conformance(tmp_path/'result.json',backend='unknown')


def test_official_source_lock_checks_inventory_and_license(tmp_path):
    import hashlib
    (tmp_path/'src').mkdir()
    names = ['src/uvm_pkg.sv','LICENSE.txt','NOTICE.txt','README.md','DEVIATIONS.md']
    for name in names: (tmp_path/name).write_bytes(b'fixture\r\n')
    lock = {'files': {name:hashlib.sha256(b'fixture\n').hexdigest() for name in names}}
    assert verify_uvm(tmp_path,lock) == lock['files']
    (tmp_path/'LICENSE.txt').write_text('modified')
    with pytest.raises(ValueError): verify_uvm(tmp_path,lock)


@pytest.mark.parametrize('additional', ['rtl_fifo/fifo_two.sv', ['../outside.sv'],
                                        ['cases/uvm_rtl_fifo.sv']])
def test_multi_source_contract_rejects_invalid_dependencies(tmp_path, additional):
    suite = tmp_path/'suite'
    shutil.copytree(default_suite(), suite)
    (tmp_path/'outside.sv').touch()
    data = load_contract(suite)
    next(case for case in data['cases'] if case['id'] == 'UVM-RTL-FIFO')['additional_sources'] = additional
    (suite/'contract.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_contract(suite)


def test_agent_and_cli_can_select_capability_check_without_project(tmp_path):
    from fpga_agent.cli import parser
    from fpga_agent.terminal import Settings,Workspace,make_plan
    args = parser().parse_args(['sv-check','--required-only'])
    assert args.required_only
    plan = make_plan(Workspace(tmp_path),Settings(),'sv-check',{},None)
    assert 'sv-check' in plan.commands[0]
    plan = make_plan(Workspace(tmp_path),Settings(),'sv-check',{'sv_backend':'modelsim'},None)
    assert plan.commands[0][-3] == 'modelsim'
    with pytest.raises(ValueError):
        make_plan(Workspace(tmp_path),Settings(),'sv-check',{'sv_backend':'modelsim; shell'},None)
    assert parser().parse_args(['sv-check','--backend','verilator']).backend=='verilator'
    plan = make_plan(Workspace(tmp_path),Settings(),'sv-check',{'sv_backend':'verilator'},None)
    assert 'verilator' in plan.commands[0]


@pytest.mark.parametrize('code,error', [(2,None),(-6,None),(None,'timeout')])
def test_cxx_build_failure_is_tool_error_and_never_a_frontend_gap(code,error):
    case=next(c for c in load_contract(default_suite())['cases'] if c['id']=='SV-CLASS')
    build={'returncode':code,'output':'error compiling generated C++'}
    if error: build['execution_error']=error
    observation={'slang':{'status':'accepted'},'compile':{'returncode':0,'output':''},'build':build}
    assert classify(case,observation)[0]=='error'


@pytest.mark.skipif(not shutil.which('iverilog') or importlib.util.find_spec('pyslang') is None,
                    reason='Icarus and optional pyslang required')
def test_real_baseline_and_tampered_capability_evidence(tmp_path):
    suite = tmp_path/'suite'
    shutil.copytree(default_suite(),suite)
    path = tmp_path/'reports/result.json'
    report = run_conformance(path,suite=suite,required_only=True)
    assert report['status'] == 'pass', report['cases']
    assert report['baseline_status'] == 'pass'
    assert len(report['cases']) == 8
    assert any(f['status']=='planned' for f in report['features'] if f['area']=='UVM')
    assert conformance_valid(path,suite=suite)[0]
    for edit in ('capability','inventory','complete','version','profile','uvm_profile'):
        changed = copy.deepcopy(report)
        if edit == 'capability': changed['features'][-1]['status']='fixture_verified'
        elif edit == 'inventory': changed['artifacts']={}
        elif edit == 'version': changed['runtime_compiler_version']='unverified version'
        elif edit == 'profile': changed['profile']='full UVM'
        elif edit == 'uvm_profile': changed['uvm_fixture_profile_status']='pass'
        else: changed['full_sv_uvm_complete']=True
        path.write_text(json.dumps(changed))
        assert not conformance_valid(path,suite=suite)[0]
    path.write_text(json.dumps(report))
    fixture = suite/'cases/integer.sv'
    fixture.write_text(fixture.read_text()+'\n// changed input\n')
    assert not conformance_valid(path,suite=suite)[0]


@pytest.mark.skipif(not all(shutil.which(t) for t in ('vlib','vlog','vsim'))
                    or importlib.util.find_spec('pyslang') is None,
                    reason='Licensed ModelSim and optional pyslang required')
def test_real_modelsim_virtual_dispatch_interface_and_evidence_binding(tmp_path):
    path = tmp_path/'reports/result.json'
    report = run_conformance(path, backend='modelsim', case_ids=['SV-CLASS','SV-INTERFACE'])
    assert report['status'] == 'pass', report['cases']
    assert report['baseline_status'] == 'not_complete'
    assert conformance_valid(path)[0]
    for edit in ('backend', 'compiled_library', 'compiled_code', 'observation'):
        changed = copy.deepcopy(report)
        if edit == 'backend':
            changed['backend'] = 'icarus'
        elif edit == 'compiled_library':
            changed['artifacts'] = {k:v for k,v in changed['artifacts'].items() if not k.endswith('work/_info')}
        elif edit == 'compiled_code':
            changed['artifacts'] = {k:v for k,v in changed['artifacts'].items() if not k.endswith('.qdb')}
        else:
            changed['cases'][0]['observation'] = changed['cases'][1]['observation']
        path.write_text(json.dumps(changed))
        assert not conformance_valid(path)[0]


@pytest.mark.skipif(not all(shutil.which(t) for t in ('vlib','vlog','vsim'))
                    or importlib.util.find_spec('pyslang') is None,
                    reason='Licensed ModelSim and optional pyslang required')
def test_real_official_uvm_profile_and_missing_reporting_terminal(tmp_path):
    path = tmp_path/'reports/result.json'
    ids = [c['id'] for c in load_contract(default_suite())['cases'] if c['kind']=='uvm-probe']
    report = run_conformance(path,backend='modelsim',case_ids=ids,timeout=120)
    assert report['status']=='pass', report['cases']
    assert report['uvm_fixture_profile_status']=='pass'
    assert report['uvm_minimum_complete'] is False
    assert all(f['status']=='fixture_verified' for f in report['features'] if f['area']=='UVM')
    assert conformance_valid(path)[0]
    changed = copy.deepcopy(report)
    command = next(c for c in changed['commands']
                   if any(Path(arg).name == 'fifo_two.sv' for arg in c['argv']))
    command['argv'] = [arg for arg in command['argv'] if Path(arg).name != 'fifo_two.sv']
    path.write_text(json.dumps(changed))
    assert conformance_valid(path) == (False, 'Compiler inputs disagree with the declared case sources')
    path.write_text(json.dumps(report))
    partial = run_conformance(tmp_path/'partial.json',backend='modelsim',case_ids=['UVM-REPORT'])
    assert partial['status']=='pass'
    assert partial['uvm_fixture_profile_status']=='not_complete'
    feature = next(f for f in partial['features'] if f['id']=='UVM-REPORT')
    assert feature['status']=='partially_verified'


@pytest.mark.skipif(not (shutil.which('verilator') or shutil.which('verilator_bin'))
                    or not (shutil.which('make') or shutil.which('mingw32-make'))
                    or not shutil.which('g++') or importlib.util.find_spec('pyslang') is None,
                    reason='Verilator, C++20 g++, make and optional pyslang required')
def test_real_verilator_dispatch_interface_and_generated_evidence(tmp_path):
    path=tmp_path/'reports/result.json'
    report=run_conformance(path,backend='verilator',case_ids=['SV-CLASS','SV-INTERFACE'],timeout=180)
    assert report['status']=='pass',report['cases']
    assert report['baseline_status']=='not_complete'
    assert report['runtime_mode']=='two-state default; timing/assert enabled'
    assert conformance_valid(path)[0]
    for edit in ('binary','generated_source','tool_version','runtime_mode'):
        changed=copy.deepcopy(report)
        if edit=='binary':
            changed['artifacts']={k:v for k,v in changed['artifacts'].items() if not k.endswith(('obj/sim','obj/sim.exe'))}
        elif edit=='generated_source':
            changed['artifacts']={k:v for k,v in changed['artifacts'].items() if not k.endswith('.cpp')}
        elif edit=='tool_version':
            changed['build_toolchain']['cxx']['version_output']='forged compiler version'
        else: changed['runtime_mode']='four-state'
        path.write_text(json.dumps(changed))
        assert not conformance_valid(path)[0]
