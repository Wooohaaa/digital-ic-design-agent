"""Independent, bounded mathematical oracles; never parse or execute the HDL.

Stimuli are shared with the DUT. Results are computed from Python integers,
explicit four-state truth tables, memory updates and a sampled state recurrence.
"""
from __future__ import annotations

import random
from collections import deque


def _decimal_pair(tokens: list[str]) -> bool:
    return len(tokens) == 2 and all(token.isascii() and token.isdecimal() and len(token) <= 2
                                    for token in tokens)


def _randomization_steps():
    return [
        *(('base', index, None, None, None) for index in range(8)),
        ('inline', 0, {4, 8}, None, None),
        ('frozen', 0, None, 5, None),
        ('frozen_bad', 0, None, 12, (12, 15)),
        ('thawed', 0, {3}, None, None),
        ('unsat', 0, {1}, None, (0, 15)),
    ]


def match_randomization(rows: list[list[str]]) -> tuple[bool, str]:
    steps = _randomization_steps()
    if len(rows) != len(steps):
        return False, f'Observation mismatch: expected {len(steps)} ordered rows, got {len(rows)}'
    for row, (scenario, index, allowed, frozen, before) in zip(rows, steps):
        if (len(row) != 5 or row[:2] != [scenario, str(index)] or row[2] not in ('0', '1')
                or not _decimal_pair(row[3:])):
            return False, f'Observation mismatch: malformed or out-of-order randomization row {scenario}/{index}'
        solutions = {(left, left+2) for left in range(2, 10)
                     if (allowed is None or left in allowed) and (frozen is None or left == frozen)}
        pair = tuple(map(int, row[3:]))
        if solutions:
            if row[2] != '1' or pair not in solutions:
                return False, f'Observation mismatch: invalid solution or status at {scenario}/{index}'
        elif row[2] != '0' or pair != before:
            return False, f'Observation mismatch: unsatisfiable {scenario}/{index} must fail without changing fields'
    return True, f'{len(steps)} ordered randomization rows satisfy the independent Python oracle'


def match_randomized_item(rows: list[list[str]]) -> tuple[bool, str]:
    domains = {40: range(2, 10), 41: (4, 8), 42: (), 43: (3, 7)}
    observations = iter(rows)
    for transaction_id, keys in domains.items():
        ident = str(transaction_id)
        sample = next(observations, [])
        if (len(sample) != 5 or sample[:2] != ['sequence', ident]
                or not _decimal_pair(sample[3:])):
            return False, f'Observation mismatch: missing, malformed or unexpected sequence row for item {ident}'
        if not keys:
            if sample[2:] != ['0', '0', '15']:
                return False, f'Observation mismatch: unsatisfiable item {ident} must fail without changing fields'
            continue
        key, value = map(int, sample[3:])
        if sample[2] != '1' or key not in keys or value != key + 2:
            return False, f'Observation mismatch: illegal solution for item {ident}'
        for stage in ('driver', 'scoreboard'):
            if next(observations, []) != [stage, ident, *sample[3:]]:
                return False, f'Observation mismatch: {stage} did not receive item {ident} unchanged and in order'
    if next(observations, None) is not None:
        return False, 'Observation mismatch: unexpected observations after the final item'
    return True, 'Three legal items reached the driver and scoreboard unchanged; one unsatisfiable item was withheld'


def match_rtl_fifo(rows: list[list[str]]) -> tuple[bool, str]:
    domains = {60: range(2, 10), 61: (4, 8), 62: (), 63: range(2, 10),
               64: (3, 7), 65: range(2, 10)}
    legal_ids = [ident for ident, keys in domains.items() if keys]
    solved, queue = {}, deque()
    next_solution = pushes = pops = cycle = 0
    input_stalls = output_stalls = simultaneous = 0
    held_input = due_score = None

    def mismatch(reason):
        return False, f'Observation mismatch: FIFO {reason}'

    for row in rows[:-1]:
        if not row or any(not t.isascii() or not t.isdecimal() or len(t) > 3 for t in row[1:]):
            return mismatch('has malformed trace data')
        kind, values = row[0], tuple(map(int, row[1:]))
        if due_score is not None:
            if kind != 'score' or values != due_score:
                return mismatch(f'scoreboard lost or changed dequeue at cycle {cycle-1}')
            due_score = None
            continue
        if kind == 'solution' and len(values) == 4 and next_solution < len(domains):
            ident, status, key, value = values
            if ident != tuple(domains)[next_solution]:
                return mismatch('solution IDs out of order')
            keys = domains[ident]
            if keys:
                if status != 1 or key not in keys or value != key + 2:
                    return mismatch(f'has illegal solution for item {ident}')
                solved[ident] = (ident, key, value)
            elif (status, key, value) != (0, 0, 15):
                return mismatch(f'unsatisfiable item {ident} did not preserve its fields')
            next_solution += 1
            continue
        if kind != 'edge' or len(values) != 11 or values[0] != cycle or cycle >= 16:
            return mismatch(f'unexpected row at cycle {cycle}')
        _, iv, ir, *signals = values
        incoming = tuple(signals[:3])
        ov, ready, *fields = signals[3:]
        outgoing = tuple(fields)
        if iv not in (0, 1) or ready != int(cycle >= 7 and cycle not in (10, 11)):
            return mismatch(f'has invalid or unexpected stimulus at cycle {cycle}')
        if ir != int(len(queue) < 2) or ov != int(bool(queue)):
            return mismatch(f'ready/valid disagrees with queue occupancy at cycle {cycle}')
        if ov and outgoing != queue[0]:
            return mismatch(f'output word differs from the queue head at cycle {cycle}')
        if held_input is not None and (not iv or incoming != held_input):
            return mismatch(f'input withdrew or changed a stalled word at cycle {cycle}')
        if iv and (pushes >= len(legal_ids) or incoming != solved.get(legal_ids[pushes])):
            return mismatch(f'input word is missing a legal source or is out of order at cycle {cycle}')
        held_input = incoming if iv and not ir else None
        input_stalls += held_input is not None
        output_stalls += bool(ov and not ready)
        if ov and ready:
            due_score = (cycle, *queue.popleft())
            pops += 1
        if iv and ir:
            queue.append(incoming)
            pushes += 1
            simultaneous += bool(ov and ready)
        cycle += 1

    if (not rows or rows[-1] != ['completed', '5'] or cycle != 16 or next_solution != len(domains)
            or pushes != len(legal_ids) or pops != pushes or queue or due_score is not None or held_input):
        return mismatch('window did not end with five completed, drained transactions')
    if not (input_stalls and output_stalls and simultaneous):
        return mismatch('window missed input/output backpressure or simultaneous transfers')
    return True, (f'Five ordered FIFO round trips, {input_stalls} blocked input edges, '
                  f'{output_stalls} blocked output edges and {simultaneous} simultaneous transfers')


def match_fifo_sync_reset(rows: list[list[str]]) -> tuple[bool, str]:
    def mismatch(reason):
        return False, f'Observation mismatch: FIFO synchronous reset {reason}'

    snapshots = {}
    edges, scores, cancelled = [], [], []
    lengths = {'before': 8, 'asserted': 8, 'sampled': 8, 'edge': 8,
               'score': 2, 'cancel': 2, 'completed': 4}
    if not rows or rows[-1] != ['completed', '2', '1', '1', '2']:
        return mismatch('did not stop the old sequence with two accepted items and one cancellation, and finish two fresh items')
    for row in rows[:-1]:
        if (not row or len(row)-1 != lengths.get(row[0], -1)
                or any(not t.isascii() or not t.isdecimal() or len(t) > 5 for t in row[1:])):
            return mismatch('has malformed samples')
        stage, values = row[0], tuple(map(int, row[1:]))
        if stage == 'edge':
            edges.append(values)
        elif stage == 'score':
            if not edges or values[0] != edges[-1][0]:
                return mismatch('published outside the claimed sampling edge')
            scores.append(values)
        elif stage == 'cancel':
            cancelled.append(values)
        elif stage in ('before', 'asserted', 'sampled') and stage not in snapshots:
            expected_count = 8 if stage == 'sampled' else 7
            if len(edges) != expected_count or (stage == 'asserted' and 'before' not in snapshots):
                return mismatch('sample is out of order around the reset edge')
            snapshots[stage] = values
        else:
            return mismatch('has a duplicate or unexpected sample')
    old = (0x460A, 0x470B, 0x480C)
    fresh = (0x4664, 0x4865)
    if snapshots != {'before': (7, 1, 1, 0, old[2], 1, 1, old[0]),
                     'asserted': (7, 0, 1, 0, old[2], 1, 1, old[0]),
                     'sampled': (7, 0, 1, 1, old[2], 0, 1, 0)}:
        return mismatch('must preserve a full queue until the reset edge and clear it after that edge')
    if cancelled != [(7, 72)] or len(edges) != 16:
        return mismatch('must observe sixteen edges and cancel the stalled request exactly once')

    queue = deque()
    pushed, expected_scores = [], []
    for cycle, (index, reset_n, valid, ready, word, out_valid, out_ready, result) in enumerate(edges):
        if (index != cycle or reset_n != int(cycle != 7) or valid not in (0, 1)
                or out_ready != int(cycle == 7 or cycle >= 12)):
            return mismatch(f'has unexpected stimulus at edge {cycle}')
        if (ready, out_valid, result) != (int(len(queue)<2), int(bool(queue)), queue[0] if queue else 0):
            return mismatch(f'port values disagree with queue at edge {cycle}')
        if cycle == 7:
            if (valid, word, len(queue)) != (1, old[2], 2):
                return mismatch('is missing a blocked third request at the reset edge')
            queue.clear()
            continue
        if valid:
            candidates = old[:2] if cycle < 5 else old[2:] if cycle < 7 else fresh
            if word not in candidates:
                return mismatch(f'has an unexpected input word at edge {cycle}')
        if out_valid and out_ready:
            expected_scores.append((cycle, queue.popleft()))
        if valid and ready:
            queue.append(word)
            pushed.append(word)
    if (pushed != [*old[:2], *fresh] or queue or scores != expected_scores
            or [word for _, word in scores] != list(fresh)):
        return mismatch('lost or leaked an item across reset, or scoreboard delivery is wrong')
    return True, 'Full queue clears on the sampling edge, one blocked request is cancelled, two fresh words delivered'


def match_fifo_repeat_reset(rows: list[list[str]]) -> tuple[bool, str]:
    def mismatch(reason):
        return False, f'Observation mismatch: repeated FIFO reset {reason}'

    if not rows or rows[-1] != ['completed', '1', '1', '1', '1', '0', '0']:
        return mismatch('did not finish both fresh sequences or left work pending')
    sizes = {'edge': 8, 'score': 2, 'pending': 6, 'cancel': 2, 'stopped': 9}
    edges, scores, pending, cancelled, stopped = [], [], [], [], []
    for row in rows[:-1]:
        if (not row or len(row)-1 != sizes.get(row[0], -1)
                or any(not t.isascii() or not t.isdecimal() or len(t)>5 for t in row[1:])):
            return mismatch('has invalid trace data')
        kind, values = row[0], tuple(map(int, row[1:]))
        cycle = values[0]
        if kind == 'edge':
            edges.append(values)
        elif kind == 'pending' and len(edges) == cycle:
            pending.append(values)
        elif kind == 'cancel' and len(edges) in (cycle, cycle+1):
            cancelled.append(values)  # Monitor and driver both wake in the active region.
        elif kind == 'stopped' and len(edges) == cycle+1:
            stopped.append(values)
        elif kind == 'score' and len(edges) == cycle+1:
            scores.append(values)
        else:
            return mismatch(f'{kind} observed outside its expected edge {cycle}')
    rounds = {7: (20490, 20491, 20492), 17: (20500, 20501, 20502)}
    fresh = [20580, 20581]
    if pending != [(cy, 1, 1, 1, 2, 0) for cy in rounds]:
        return mismatch('did not have an exclusive holder and a requested but ungranted competitor in both rounds')
    if cancelled != [(cy, words[2]) for cy, words in rounds.items()]:
        return mismatch('did not cancel exactly the two stalled requests')
    if stopped != [(cy, 1, 1, 0, 0, 2, 0, 0, 0) for cy in rounds]:
        return mismatch('left a sequence, lock, pending grant, or buffered output after reset')
    if len(edges) != 26:
        return mismatch('did not observe all 26 sampling edges')

    fifo = deque()
    expected_scores, pushed = [], []
    held = None
    for cycle, (index, reset_n, iv, ir, word, ov, ready, result) in enumerate(edges):
        if (index, reset_n, ready) != (cycle, int(cycle not in rounds), int(cycle in rounds or cycle>=24)):
            return mismatch(f'has incorrect stimulus at edge {cycle}')
        if (ir, ov, result) != (int(len(fifo)<2), int(bool(fifo)), fifo[0] if fifo else 0):
            return mismatch(f'port values disagree with the queue at edge {cycle}')
        if iv not in (0, 1) or held is not None and (iv != 1 or word != held):
            return mismatch(f'input withdrew or changed a stalled request at edge {cycle}')
        if not reset_n:
            words = rounds[cycle]
            if list(fifo) != list(words[:2]) or (iv, word, held) != (1, words[2], words[2]):
                return mismatch(f'did not reset a full queue with a previously stalled request at edge {cycle}')
            fifo.clear()
            held = None
            continue
        if iv and word not in (rounds[7] if cycle<7 else rounds[17] if cycle<17 else fresh):
            return mismatch(f'presented a competitor or a stale request at edge {cycle}')
        held = word if iv and not ir else None
        if ov and ready:
            expected_scores.append((cycle, fifo.popleft()))
        if iv and ir:
            fifo.append(word)
            pushed.append(word)
    if (pushed != [*rounds[7][:2], *rounds[17][:2], *fresh]
            or fifo or held is not None or scores != expected_scores
            or [word for _, word in scores] != fresh):
        return mismatch('did not drain exactly the two fresh words without leakage')
    return True, 'Two full FIFO resets stopped both contenders and cleared locks; only two fresh words delivered'


def match_fifo_reset_priority(rows: list[list[str]]) -> tuple[bool, str]:
    observations = iter(rows)
    queue = deque()
    trial = 0
    for occupied in range(3):
        for demand in range(4):
            for reset in (0, 1):
                tag = 0x1000 + 16 * trial
                fill = [(1, 1, 0, tag + n) for n in range(1, occupied+1)]
                conflict = (1-reset, demand >> 1, demand & 1, tag+3)
                read = (1, 0, 1, 0)
                refill = [(1, 1, 0, tag+4), (1, 1, 0, tag+5)]
                for step, (run, send, receive, word) in enumerate(fill+[conflict, read, read]+refill+[read]*3):
                    before = (int(len(queue)<2), int(bool(queue)), queue[0] if queue else 0)
                    if not run:
                        queue.clear()
                    else:
                        if before[1] and receive:
                            queue.popleft()
                        if before[0] and send:
                            queue.append(word)
                    after = (int(len(queue)<2), int(bool(queue)), queue[0] if queue else 0)
                    expected_row = list(map(str, (trial, step, run, send, receive, word, *before, *after)))
                    if next(observations, None) != expected_row:
                        return False, (f'Observation mismatch: FIFO reset priority at occupancy {occupied}, '
                                       f'send {conflict[1]}, receive {conflict[2]}, reset {reset}, step {step}')
                trial += 1
    if next(observations, None) is not None:
        return False, 'Observation mismatch: FIFO reset priority has unexpected extra samples'
    return True, '24 FIFO boundary scenarios and 216 edges match reset priority and recovery'


TRACE_ORACLES = {
    'SV-RANDOMIZE': match_randomization,
    'UVM-RANDOMIZED-ITEM': match_randomized_item,
    'UVM-RTL-FIFO': match_rtl_fifo,
    'UVM-FIFO-SYNC-RESET': match_fifo_sync_reset,
    'UVM-FIFO-REPEAT-RESET': match_fifo_repeat_reset,
    'SV-FIFO-RESET-PRIORITY': match_fifo_reset_priority,
}


def vectors() -> list[tuple[int, int]]:
    edges = [(0, 0), (0, 1), (255, 1), (128, 127), (127, 128),
             (255, 255), (128, 128), (1, 255), (85, 170), (170, 85)]
    rng = random.Random(0x525349)
    return edges + [(rng.randrange(256), rng.randrange(256)) for _ in range(64-len(edges))]


def _strings(rows):
    return [[str(value) for value in row] for row in rows]


def expected(case: str) -> list[list[str]]:
    data = vectors()
    if case == 'SV-INT':
        signed = lambda value: value if value < 128 else value-256
        return _strings((i, (a+b) % 256, (signed(a) >> 3) % 256,
                         int(signed(a) < signed(b)), int(a < b), a ^ b)
                        for i, (a, b) in enumerate(data))
    if case == 'SV-PACKED':
        return _strings((i, a // 16, a % 16, 8, 2, 2) for i, (a, _) in enumerate(data))
    if case == 'SV-PACKAGE':
        return _strings((i, ((a * 2) % 256) + a // 128, (a+7) % 256)
                        for i, (a, _) in enumerate(data))
    if case == 'SV-GENERATE':
        return _strings((i, a ^ 0x55, 8) for i, (a, _) in enumerate(data))
    if case == 'SV-NBA':
        state = 0
        rows = []
        for i in range(32):
            previous, state = state, (state+1) % 256
            rows.append((i, state, previous))
        return _strings(rows)
    if case == 'SV-ARRAY':
        memory = [0] * 8
        rows = []
        for i, (a, b) in enumerate(data):
            memory[i % 8] = a ^ b
            rows.append((i, memory[i % 8], memory[(i+3) % 8]))
        return _strings(rows)
    if case == 'SV-4STATE':
        states = '01xz'
        rows = []
        for i, a in enumerate(states):
            for j, b in enumerate(states):
                both_defined = a in '01' and b in '01'
                conjunction = '0' if '0' in (a, b) else '1' if a == b == '1' else 'x'
                disjunction = '1' if '1' in (a, b) else '0' if a == b == '0' else 'x'
                xor = str(int(a) ^ int(b)) if both_defined else 'x'
                rows.append((i, j, conjunction, disjunction, xor, int(a == b)))
        return _strings(rows)
    if case == 'SV-CLASS':
        return [['0', '12'], ['1', '18'], ['2', '18']]
    if case == 'SV-INTERFACE':
        return _strings((i, (a+b) % 256) for i, (a, b) in enumerate(data))
    if case == 'UVM-PHASE':
        return [['0', '1'], ['1', '1'], ['2', '1']]
    if case == 'UVM-COMPONENT':
        return [['0','1','1','1'], ['1','1','1','0']]
    if case == 'UVM-FACTORY':
        return [['0','7','1'], ['1','29','1','1'], ['2','7','0']]
    if case == 'UVM-CONFIG':
        return [['0','1','17','0','-2'], ['1','1','29','0','-2']]
    if case == 'UVM-CONFIG-PRECEDENCE':
        left, right, excluded = ('uvm_test_top.lane_a.sink', 'uvm_test_top.lane_b.sink', 'uvm_test_top.lane_a.tap')
        return _strings([
            ('build', left, 1, 23), ('build', right, 1, 23), ('build', excluded, 0, -1),
            ('leaf_first', left, 1, 41), ('test_next', left, 1, 53), ('leaf_last', left, 1, 61),
            ('isolation', right, 1, 23), ('isolation', excluded, 0, -1),
        ])
    if case == 'UVM-ANALYSIS':
        return [['0','4','4']] + _strings((i+1,3*i+5,3*i+5,1,1) for i in range(4))
    if case == 'UVM-SEQUENCE':
        return [['0','4','4']] + _strings((i+1,5*i+3) for i in range(4))
    if case == 'UVM-ARBITRATION':
        grants = [(2,1), (3,1), (2,2), (3,2), (1,1), (1,2)]
        return _strings([
            ('counts', 6, 6),
            *(('grant', i, owner, tx, 10*owner+tx) for i,(owner,tx) in enumerate(grants)),
            *(('sent', i, owner, tx) for i,(owner,tx) in enumerate(reversed(grants))),
            *(('received', owner, tx-1, owner, tx, 3*(10*owner+tx)+7, 1)
              for owner in (1,2,3) for tx in (1,2)),
        ])
    if case == 'UVM-LOCK-GRAB':
        grants = (11, 12, 31, 21, 41)
        return _strings([
            ('queued', 1, 1, 1, 1),
            *(('grant', i, value, int(value != 41), int(value != 41)) for i,value in enumerate(grants)),
            *(('ownership', owner, 1, 1) for owner in (1,2,3)),
            ('remaining', 0, 0),
        ])
    if case == 'UVM-STOP-RESTART':
        return _strings([
            ('first', 11), ('pending', 12, 1, 1, 1, 1),
            ('stopped', 1, 1, 0, 0, 0, 0, 1),
            ('fresh', 21, 7, 0), ('response', 0, 121, 7, 0, 1),
        ])
    if case == 'UVM-RTL-RESET':
        return _strings([
            ('buffered', 47, 0, 0, 1),
            ('async', 1, 0, 0, 0),
            ('cancelled', 1, 0, 0, 1, 0),
            ('recovery', 1, 97, 7, 1, 1, 0),
        ])
    if case == 'UVM-REPORT':
        return [['0','2','1','0','0']]
    if case == 'UVM-FATAL':
        return [['0','1']]
    raise ValueError(f'No independent oracle for {case}')
