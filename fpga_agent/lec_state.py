"""A checked bitwise state relation reduced to a combinational SAT obligation.

The relation is a bijection, not an assumed equality of observed outputs:
initial-state sets agree under it, and SAT proves output/transition preservation
for ALL corresponding Boolean states (including unreachable ones) and inputs.
Only one external positive-edge clock is admitted. Asynchronous $adff reset
assertion is a separately checked relation-preserving event, never async2sync.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path


# A closed set prevents accidentally treating stateful, formal, or opaque cells
# as combinational. Yosys SAT must still model every admitted cell successfully.
COMBINATIONAL = {
    '$not', '$pos', '$neg', '$and', '$or', '$xor', '$xnor', '$reduce_and',
    '$reduce_or', '$reduce_xor', '$reduce_xnor', '$reduce_bool', '$logic_not',
    '$logic_and', '$logic_or', '$shl', '$shr', '$sshl', '$sshr', '$shift',
    '$shiftx', '$lt', '$le', '$eq', '$ne', '$eqx', '$nex', '$ge', '$gt',
    '$add', '$sub', '$mul', '$div', '$mod', '$divfloor', '$modfloor', '$pow',
    '$mux', '$pmux', '$bmux', '$demux', '$concat', '$slice', '$lut',
}


def _int(value) -> int:
    return value if type(value) is int else int(value, 2)


def _read(path: Path, top: str) -> dict:
    modules = json.loads(path.read_text(encoding='utf-8'))['modules']
    if set(modules) != {top}:
        raise ValueError('State proof requires one fully flattened module')
    module = modules[top]
    if module.get('memories') or any(_int(module.get('attributes', {}).get(k, 0))
                                       for k in ('blackbox', 'whitebox')):
        raise ValueError('Opaque modules or unmapped memories are unsupported')
    # Newer Yosys flatten adds hierarchy metadata with no pins or logic.
    for name, cell in list(module['cells'].items()):
        if cell['type'] == '$scopeinfo' and not cell['connections']:
            del module['cells'][name]
    return module


def _inventory(module: dict) -> tuple[dict, dict, dict]:
    inputs, state, initial = {}, {}, {}
    for name, port in module['ports'].items():
        if port['direction'] not in {'input', 'output'}:
            raise ValueError('Inout ports are unsupported in state proof')
        if port['direction'] == 'input':
            for index, bit in enumerate(port['bits']):
                if type(bit) is not int or bit in inputs:
                    raise ValueError('Aliased or constant primary inputs are unsupported')
                inputs[bit] = (name, index)
    for name, cell in module['cells'].items():
        kind = cell['type']
        if kind in COMBINATIONAL:
            continue
        if kind not in {'$dff', '$adff'}:
            raise ValueError(f'Unsupported state/opaque cell: {kind} ({name})')
        c, p = cell['connections'], cell['parameters']
        if set(c) != ({'CLK', 'D', 'Q', 'ARST'} if kind == '$adff' else {'CLK', 'D', 'Q'}) or _int(p['CLK_POLARITY']) != 1 or len(c['CLK']) != 1:
            raise ValueError('Only positive-edge $dff/$adff state is supported')
        clock = inputs.get(c['CLK'][0])
        if clock is None or len(module['ports'][clock[0]]['bits']) != 1:
            raise ValueError('State clock must be a scalar primary input')
        if len(c['Q']) != len(c['D']) or len(c['Q']) != _int(p['WIDTH']):
            raise ValueError('Malformed state width')
        reset, reset_bits = None, [None] * len(c['Q'])
        if kind == '$adff':
            if len(c['ARST']) != 1:
                raise ValueError('Asynchronous reset must be scalar')
            reset = inputs.get(c['ARST'][0])
            value = p['ARST_VALUE']
            if reset is None or len(module['ports'][reset[0]]['bits']) != 1 or reset == clock:
                raise ValueError('Asynchronous reset must be an independent scalar primary input')
            if _int(p['ARST_POLARITY']) not in {0, 1} or not isinstance(value, str) or len(value) != len(c['Q']) or set(value) - {'0', '1'}:
                raise ValueError('Unsupported asynchronous reset value/polarity')
            reset_bits = list(reversed(value))
        for q, d, reset_value in zip(c['Q'], c['D'], reset_bits):
            if type(q) is not int or q in state or q in inputs:
                raise ValueError('State bits must have unique non-input drivers')
            state[q] = {'next': d, 'clock': clock, 'reset':
                        [*reset, _int(p['ARST_POLARITY']), reset_value] if reset else None}
    if len({s['clock'] for s in state.values()}) > 1:
        raise ValueError('Multiple clocks are unsupported in state proof')
    for net in module['netnames'].values():
        init = net.get('attributes', {}).get('init')
        if init is None:
            continue
        bits = net['bits']
        if not isinstance(init, str) or len(init) != len(bits) or set(init) - set('01x'):
            raise ValueError('Unsupported initialization encoding')
        for bit, value in zip(bits, reversed(init)):
            if value == 'x':
                continue
            if bit not in state or bit in initial and initial[bit] != value:
                raise ValueError('Conflicting or non-state initialization')
            initial[bit] = value
    return inputs, state, initial


def _labels(module: dict, state: dict) -> dict:
    labels = {}
    for name, net in module['netnames'].items():
        if net.get('hide_name') or name.startswith('$'):
            continue
        for index, bit in enumerate(net['bits']):
            if bit in state:
                # Direction is included so reversed declarations cannot imply
                # a reversed state mapping silently. Bit order is JSON LSB-first.
                label = (name, net.get('offset', 0), net.get('upto', 0), index)
                labels[label] = bit
    return labels


def _reset_classes(gold, gate, gs, ts, gl, tl, reset_start):
    """Complete cross-design equality classes with a proven async-reset base.

    Aliases only propose the invariant. Reset establishes it, and the generated
    SAT obligation checks EVERY member's next value, including same-side merges.
    """
    signal, active = reset_start['signal'], reset_start['active']
    for module in (gold, gate):
        port = module['ports'].get(signal, {})
        if port.get('direction') != 'input' or len(port.get('bits', [])) != 1:
            raise ValueError('reset_start.signal must be a scalar primary input in both designs')
    if not gs or not ts:
        raise ValueError('Reset-state relation requires state in both designs')
    parent = {(side, bit): (side, bit) for side, state in enumerate((gs, ts)) for bit in state}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    witnesses = {}
    for label in sorted(gl.keys() & tl.keys()):
        a, b = (0, gl[label]), (1, tl[label])
        parent[find(b)] = find(a)
        witnesses.setdefault(a, list(label))
        witnesses.setdefault(b, list(label))
    groups = {}
    for node in sorted(parent):
        groups.setdefault(find(node), [[], []])[node[0]].append(node[1])
    classes = sorted(groups.values(), key=lambda group: (group[0], group[1]))
    for left, right in classes:
        if not left or not right:
            raise ValueError('Incomplete reset-state relation: every state bit needs a cross-design class')
        members = [gs[bit] for bit in left] + [ts[bit] for bit in right]
        expected = members[0]['reset']
        if expected is None or expected[:3] != [signal, 0, active]:
            raise ValueError('Reset base requires every state bit to use the declared asynchronous reset')
        if any(s['reset'] != expected for s in members):
            raise ValueError('Reset base fails: class members have different reset events or values')
        if len({s['clock'] for s in members}) != 1:
            raise ValueError('Corresponding state bits use different clocks')
    return classes, witnesses


def build_state_miter(gold_path: Path, gate_path: Path, top: str, *, reset_start: dict | None = None) -> tuple[dict, dict]:
    gold, gate = _read(gold_path, 'gold'), _read(gate_path, top)
    gi, gs, ginit = _inventory(gold)
    ti, ts, tinit = _inventory(gate)
    if set(gold['ports']) != set(gate['ports']):
        raise ValueError('Golden/gate port names differ')
    for name, port in gold['ports'].items():
        other = gate['ports'][name]
        if any(port.get(key, 0) != other.get(key, 0) for key in ('direction', 'offset', 'upto', 'signed')) or len(port['bits']) != len(other['bits']):
            raise ValueError(f'Golden/gate port signature differs: {name}')
    gl, tl = _labels(gold, gs), _labels(gate, ts)
    mapping, reverse = {}, {}
    witnesses = {}
    if reset_start is not None:
        classes, class_witnesses = _reset_classes(gold, gate, gs, ts, gl, tl, reset_start)
    else:
        for label in sorted(gl.keys() & tl.keys()):
            a, b = gl[label], tl[label]
            if a in mapping and mapping[a] != b or b in reverse and reverse[b] != a:
                raise ValueError(f'Ambiguous state correspondence at {label[0]}[{label[-1]}]: '
                                 f'gold bit {a}, gate bit {b}; a bijection cannot represent merged state')
            mapping[a], reverse[b] = b, a
            witnesses.setdefault(a, list(label))
        if set(mapping) != set(gs) or set(reverse) != set(ts):
            raise ValueError(f'Incomplete state bijection: gold={len(gs)}, gate={len(ts)}, matched={len(mapping)}')
        for a, b in mapping.items():
            if gs[a]['clock'] != ts[b]['clock']:
                raise ValueError('Corresponding state bits use different clocks')
            if gs[a]['reset'] != ts[b]['reset']:
                raise ValueError('Asynchronous reset event does not preserve the state relation')
            if ginit.get(a, 'x') != tinit.get(b, 'x'):
                raise ValueError('Initial-state sets differ under the state bijection')
        classes = [([a], [mapping[a]]) for a in sorted(mapping)]

    module = {'attributes': {'top': '1'}, 'ports': {}, 'cells': {}, 'netnames': {}}
    next_bit = 2

    def alloc():
        nonlocal next_bit
        value = next_bit
        next_bit += 1
        return value

    maps = [{}, {}]

    def port(name, direction, bits):
        module['ports'][name] = {'direction': direction, 'bits': bits}
        module['netnames'][name] = {'hide_name': 0, 'bits': bits, 'attributes': {}}

    for index, (name, entry) in enumerate(sorted(gold['ports'].items())):
        if entry['direction'] != 'input':
            continue
        bits = [alloc() for _ in entry['bits']]
        port(f'input_{index}', 'input', bits)
        for side, original in enumerate((entry, gate['ports'][name])):
            maps[side].update(zip(original['bits'], bits))
    states = sorted(gs)
    for index, (left, right) in enumerate(classes):
        shared = alloc()
        for side, bits in enumerate((left, right)):
            for bit in bits:
                maps[side][bit] = shared
        port(f'state_{index}', 'input', [shared])

    def remap(side, bit):
        if type(bit) is not int:
            if bit not in {'0', '1', 'x', 'z'}:
                raise ValueError('Invalid netlist bit')
            return bit
        if bit not in maps[side]:
            maps[side][bit] = alloc()
        return maps[side][bit]

    for side, original in enumerate((gold, gate)):
        for index, cell in enumerate(original['cells'].values()):
            if cell['type'] in {'$dff', '$adff'}:
                continue  # Q became a common state input; D is checked below.
            copied = copy.deepcopy(cell)
            copied['attributes'] = {}
            copied['connections'] = {p: [remap(side, b) for b in bits]
                                     for p, bits in cell['connections'].items()}
            module['cells'][f'side_{side}_cell_{index}'] = copied

    comparisons = []

    def compare(label, left, right):
        if not left or len(left) != len(right):
            raise ValueError('Empty or mismatched proof obligation')
        out = alloc()
        module['cells'][f'compare_{len(comparisons)}'] = {
            'hide_name': 0, 'type': '$eqx', 'parameters': {
                'A_SIGNED': '0', 'B_SIGNED': '0', 'A_WIDTH': len(left), 'B_WIDTH': len(right), 'Y_WIDTH': 1},
            'attributes': {}, 'port_directions': {'A': 'input', 'B': 'input', 'Y': 'output'},
            'connections': {'A': left, 'B': right, 'Y': [out]}}
        comparisons.append(out)
        port(label, 'output', [out])

    output_bits = 0
    for index, (name, entry) in enumerate(sorted(gold['ports'].items())):
        if entry['direction'] == 'output':
            compare(f'output_equal_{index}', [remap(0, b) for b in entry['bits']],
                    [remap(1, b) for b in gate['ports'][name]['bits']])
            output_bits += len(entry['bits'])
    if not output_bits:
        raise ValueError('No observable output ports')

    clock_next_cache = {}

    def clock_next(side, original, inventory, bit):
        if (side, bit) in clock_next_cache:
            return clock_next_cache[(side, bit)]
        state = inventory[bit]
        data = remap(side, state['next'])
        if state['reset'] is None:
            return data
        name, index, polarity, value = state['reset']
        reset = remap(side, original['ports'][name]['bits'][index])
        out = alloc()
        module['cells'][f'reset_mux_{side}_{bit}'] = {
            'hide_name': 0, 'type': '$mux', 'attributes': {}, 'parameters': {'WIDTH': 1},
            'port_directions': {'A': 'input', 'B': 'input', 'S': 'input', 'Y': 'output'},
            'connections': {'A': [data if polarity else value], 'B': [value if polarity else data],
                            'S': [reset], 'Y': [out]}}
        clock_next_cache[(side, bit)] = out
        return out

    if states:
        if reset_start is None:
            compare('next_state_equal', [clock_next(0, gold, gs, a) for a in states],
                    [clock_next(1, gate, ts, mapping[a]) for a in states])
        else:
            actual, expected = [], []
            for left, right in classes:
                reference = clock_next(0, gold, gs, left[0])
                for side, bits, original, inventory in ((0, left, gold, gs), (1, right, gate, ts)):
                    for bit in bits:
                        actual.append(clock_next(side, original, inventory, bit))
                        expected.append(reference)
            compare('next_state_equal', actual, expected)
    ok = alloc()
    module['cells']['all_obligations'] = {
        'hide_name': 0, 'type': '$reduce_and', 'attributes': {},
        'parameters': {'A_SIGNED': '0', 'A_WIDTH': len(comparisons), 'Y_WIDTH': 1},
        'port_directions': {'A': 'input', 'Y': 'output'},
        'connections': {'A': comparisons, 'Y': [ok]}}
    port('proof_ok', 'output', [ok])
    relation = {} if reset_start is not None else {'method': 'state-bijection', 'state_bits': len(states), 'output_bits': output_bits,
                'initial_state_sets': 'identical under bijection',
                'clock': list(gs[states[0]]['clock']) if states else None,
                'input_domain': 'all defined Boolean inputs and corresponding state bits',
                'obligations': ['all outputs equal', 'all clock next-state bits equal',
                                'all asynchronous reset events preserve relation'],
                'mapping': [{'gold': a, 'gate': mapping[a], 'witness': witnesses[a],
                             'init': ginit.get(a, 'x'), 'async_reset': gs[a]['reset']} for a in states]}
    if reset_start is not None:
        relation = {'method': 'reset-state-relation', 'state_bits': len(gs), 'gate_state_bits': len(ts),
                    'state_classes': len(classes), 'output_bits': output_bits,
                    'clock': list(gs[states[0]]['clock']),
                    'reset_start': dict(reset_start),
                    'initial_state_contract': 'after assertion of the declared asynchronous reset; pre-reset behavior excluded',
                    'base_case': 'every state bit resets; all members of each class have the same known reset value',
                    'input_domain': 'all defined Boolean inputs and all states satisfying the complete equality relation',
                    'obligations': ['all outputs equal', 'every gold and gate next-state bit preserves its class',
                                    'every asynchronous reset event re-establishes relation'],
                    'transition_bits_checked': len(gs) + len(ts),
                    'classes': [{'gold': left, 'gate': right, 'reset_value': gs[left[0]]['reset'][3],
                                 'witnesses': [class_witnesses[(side, bit)] for side, bits in enumerate((left, right)) for bit in bits]}
                                for left, right in classes]}
    return {'creator': 'IC Forge state-bijection miter', 'modules': {'state_miter': module}}, relation


def state_proof_valid(run: Path, top: str, *, reset_start: dict | None = None) -> tuple[int, dict]:
    """Rebuild the relation and miter so editable metadata cannot omit bits."""
    miter, relation = build_state_miter(run / 'gold.json', run / 'gate.json', top, reset_start=reset_start)
    if json.loads((run / 'state_miter.json').read_text(encoding='utf-8')) != miter:
        raise ValueError('State miter differs from the checked netlists')
    if json.loads((run / 'state_relation.json').read_text(encoding='utf-8')) != relation:
        raise ValueError('State relation differs from the complete inventory')
    # This marker is emitted by Yosys only after sat -verify completes. It is
    # checked together with stage returncode and all bound artifacts by lec.py.
    log = (run / 'lec.log').read_text(encoding='utf-8')
    if 'SAT proof finished - no model found: SUCCESS!' not in log.split('[state_proof]\n')[-1]:
        raise ValueError('Successful SAT proof evidence missing')
    return relation['state_bits'] + relation['output_bits'], relation
