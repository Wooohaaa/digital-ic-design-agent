"""Terminal-native block lettering and the IC Forge startup card."""
from __future__ import annotations

from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

GOLD = '#ffd21f'
COPPER = '#bd845f'
IVORY = '#e7e4ce'
MUTED = '#8e824a'

# Each letter is made from terminal cells, including its outlined drop shadow.
# No installed display font, image, or font-rendering dependency is involved.
BLOCK_LETTERS = {
    'I': ('██╗', '██║', '██║', '██║', '██║', '╚═╝'),
    'C': (' ██████╗', '██╔════╝', '██║     ', '██║     ', '╚██████╗', ' ╚═════╝'),
    'F': ('███████╗', '██╔════╝', '█████╗  ', '██╔══╝  ', '██║     ', '╚═╝     '),
    'O': (' ██████╗ ', '██╔═══██╗', '██║   ██║', '██║   ██║', '╚██████╔╝', ' ╚═════╝ '),
    'R': ('██████╗ ', '██╔══██╗', '██████╔╝', '██╔══██╗', '██║  ██║', '╚═╝  ╚═╝'),
    'G': (' ██████╗ ', '██╔════╝ ', '██║  ███╗', '██║   ██║', '╚██████╔╝', ' ╚═════╝ '),
    'E': ('███████╗', '██╔════╝', '█████╗  ', '██╔══╝  ', '███████╗', '╚══════╝'),
}
SMALL_LETTERS = {
    'I': ('███', ' █ ', ' █ ', ' █ ', '███'),
    'C': ('███', '█  ', '█  ', '█  ', '███'),
    'F': ('███', '█  ', '██ ', '█  ', '█  '),
    'O': ('███', '█ █', '█ █', '█ █', '███'),
    'R': ('██ ', '█ █', '██ ', '█ █', '█ █'),
    'G': ('███', '█  ', '█ █', '█ █', '███'),
    'E': ('███', '█  ', '██ ', '█  ', '███'),
}


def wordmark(width: int) -> Text:
    letters = BLOCK_LETTERS if width >= 62 else SMALL_LETTERS
    rows = len(letters['I'])
    palette = ('#ffdc24', '#ffd21f', '#fbc129', '#edac42', '#d98d63', '#a76f51')
    text = Text(no_wrap=True, overflow='crop')
    for row in range(rows):
        line = ' '.join(letters[letter][row] for letter in 'ICFORGE').rstrip()
        for cell in line:
            text.append(cell, style=palette[row] if cell == '█' else COPPER)
        if row < rows - 1:
            text.append('\n')
    return text


def chip_art() -> Text:
    inside = [
        '                       ',
        '    ██╗  ██████╗       ',
        '    ██║ ██╔════╝       ',
        '    ██║ ██║            ',
        '    ██║ ╚██████╗       ',
        '    ╚═╝  ╚═════╝       ',
        '                       ',
    ]
    lines = ['       ╷  ╷  ╷  ╷  ╷  ╷', '    ╭──┴──┴──┴──┴──┴──┴──╮']
    lines += [' ───┤' + line.ljust(23) + '├───' for line in inside]
    lines += ['    ╰──┬──┬──┬──┬──┬──┬──╯', '       ╵  ╵  ╵  ╵  ╵  ╵', '', '       S P E C  →  R T L']
    text = Text(no_wrap=True)
    for row, line in enumerate(lines):
        text.append(line, style=GOLD if 2 <= row <= 7 else COPPER)
        if row < len(lines)-1:
            text.append('\n')
    return text


def startup_panel(*, width: int, height: int, version: str, provider: str, model: str,
                  workspace: Path, project: str, session: str, connected: bool,
                  toolchain: dict[str, bool], command_count: int) -> Panel:
    short = height < 36
    title = Text(f' ICFORGE v{version} ', style=f'bold {GOLD}')
    if width < 70:
        content = Text()
        content.append(f'{provider} / {model}\n', style=GOLD)
        content.append(f'{project or workspace.name} · {session}\n\n', style=MUTED)
        content.append('Available Tools\n', style=f'bold {GOLD}')
        content.append('/design /cpu /verify /suite\n/td /flow /lint /review\n/package /project /connect', style=IVORY)
        return Panel(content, title=title, border_style=COPPER, box=box.ROUNDED, padding=(0, 1))

    left = Text()
    if not short and width >= 108:
        left.append_text(chip_art())
        left.append('\n\n')
    left.append(model+'\n', style=f'bold {GOLD}')
    state = 'configured' if connected else 'offline replay' if provider == 'exchange' else '/connect to configure'
    left.append(f'{provider} · {state}\n', style=MUTED)
    left.append(str(workspace)+'\n', style=MUTED)
    left.append(f'Session: {session}', style='#716d5c')

    right = Text()
    right.append('Available Tools\n', style=f'bold {GOLD}')
    categories = [
        ('design', '/design  /cpu  /init'),
        ('verification', '/verify  /suite  /replay'),
        ('implementation', '/synth  /td  /flow'),
        ('engineering', '/lint  /review'),
        ('delivery', '/package  /artifacts  /export'),
        ('workspace', '/project  /files  /open  /diff'),
    ]
    for category, commands in categories:
        right.append(category+': ', style=MUTED)
        right.append(commands+'\n', style=IVORY)
    if not short:
        right.append('\nDesign Workflows\n', style=f'bold {GOLD}')
        right.append('RTL design: ', style=MUTED)
        right.append('spec → RTL → self-check simulation\n', style=IVORY)
        right.append('RISC-V CPU: ', style=MUTED)
        right.append('single / multi / pipeline · RV32I\n', style=IVORY)
        right.append('FPGA delivery: ', style=MUTED)
        right.append('Anlogic TD · HX1P35A\n', style=IVORY)
        right.append('Engineering: ', style=MUTED)
        right.append('evidence gates · traceable review\n', style=IVORY)
        right.append('\nLocal Toolchain\n', style=f'bold {GOLD}')
        for name, available in toolchain.items():
            right.append(name+': ', style=MUTED)
            right.append('available  ' if available else 'not installed  ', style=IVORY if available else COPPER)
        right.append(f'\n\n{command_count} commands · /help for commands', style=MUTED)
    else:
        right.rstrip()

    grid = Table.grid(padding=(0, 3))
    grid.width = max(20, width - (6 if not short else 4))
    grid.expand = True
    grid.add_column(width=34 if width >= 108 else 25, no_wrap=False, overflow='fold')
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return Panel(grid, title=title, border_style=COPPER, box=box.ROUNDED,
                 padding=(1, 2) if not short else (0, 1), expand=True)
