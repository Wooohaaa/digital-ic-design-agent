"""Terminal-native IC Forge marks, color themes, and startup card."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


@dataclass(frozen=True)
class Palette:
    name: str
    description: str
    line: str
    muted: str
    accent: str
    secondary: str
    foreground: str
    highlight: str
    cursor: str
    success: str
    warning: str
    error: str

    def css_variables(self) -> dict[str, str]:
        return {
            'forge-bg': '#000000',
            'forge-panel': '#000000',
            'forge-line': self.line,
            'forge-muted': self.muted,
            'forge-accent': self.accent,
            'forge-secondary': self.secondary,
            'forge-foreground': self.foreground,
            'forge-highlight': self.highlight,
            'forge-cursor': self.cursor,
            'forge-selected': '#000000',
            'forge-overlay': '#000000 85%',
            'forge-code': '#000000',
            'forge-log': '#000000',
            'forge-success': self.success,
            'forge-warning': self.warning,
            'forge-error': self.error,
        }


THEMES: dict[str, Palette] = {
    'forge': Palette(
        'Forge Gold', 'Gold, ivory, and copper', '#ad795a', '#a99874', '#ffd21f',
        '#e6bd67', '#e7e4ce', '#fbc129', '#20bce8', '#7ccf9a', '#efc884', '#ff7b72',
    ),
    'rose': Palette(
        'Rose Milk', 'Rose red and porcelain pink', '#d78d99', '#f1dddf', '#e72d48',
        '#c91f3b', '#f1dddf', '#ff8fa3', '#ffc0cb', '#86d6a0', '#ffd166', '#ff7189',
    ),
    'citrine': Palette(
        'Citrine Grove', 'Citron yellow and leaf green', '#88ae1b', '#b2cd56', '#ffe76f',
        '#88ae1b', '#fff2a9', '#ffe76f', '#c9e979', '#b2f56c', '#ffd166', '#ff8677',
    ),
    'nord': Palette(
        'Nord Frost', 'Arctic slate and glacier blue', '#5e81ac', '#d8dee9', '#88c0d0',
        '#a3be8c', '#eceff4', '#8fbcbb', '#88c0d0', '#a3be8c', '#ebcb8b', '#ff8892',
    ),
    'tokyo': Palette(
        'Tokyo Night', 'Indigo night and neon cyan', '#565f89', '#9aa5ce', '#7dcfff',
        '#bb9af7', '#c0caf5', '#73daca', '#2ac3de', '#9ece6a', '#e0af68', '#f7768e',
    ),
    'mocha': Palette(
        'Catppuccin Mocha', 'Soft pastel accents and lilac', '#6c7086', '#a6adc8', '#cba6f7',
        '#f5c2e7', '#cdd6f4', '#89dceb', '#f5e0dc', '#a6e3a1', '#f9e2af', '#f38ba8',
    ),
    'latte': Palette(
        'Catppuccin Latte', 'Warm rose, sky blue, and lavender', '#7287fd', '#bcc0cc', '#ea76cb',
        '#04a5e5', '#eff1f5', '#c4a7e7', '#88d8e8', '#40a02b', '#df8e1d', '#d20f39',
    ),
    'mint': Palette(
        'Carbon Mint', 'Graphite, jade, and acid lime', '#4f9f8d', '#98bdb3', '#67e8c7',
        '#b2f56c', '#e0f5ee', '#53cdae', '#b2f56c', '#b2f56c', '#f0ca75', '#ff7d78',
    ),
    'cobalt': Palette(
        'Cobalt Signal', 'Ink black and precise electric blue', '#54749d', '#9aabc2', '#7aa2f7',
        '#56c5d0', '#e2eaf7', '#a9c1ff', '#56c5d0', '#79d49a', '#e9bd6b', '#ff7f86',
    ),
}

LOGOS = {
    'block': 'Block Forge · outlined terminal lettering',
    '3d': 'Isometric · extruded ASCII lettering',
    'glitch': 'Pixel Glitch · fragmented block lettering',
    'minimal': 'Minimal · framed monogram wordmark',
}


# Each letter is built from terminal cells, not a display font or bitmap asset.
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


def theme_options() -> list[tuple[str, str]]:
    return [(key, f'{palette.name}   {palette.accent} / {palette.secondary}') for key, palette in THEMES.items()]


def logo_options() -> list[tuple[str, str]]:
    return list(LOGOS.items())


def _joined_letters(font: dict[str, tuple[str, ...]], word: str = 'ICFORGE') -> list[str]:
    rows = []
    for row in range(len(font[word[0]])):
        rows.append(' '.join(font[letter][row] for letter in word).rstrip())
    return rows


def _paint_lines(lines: list[str], palette: Palette, *, front: str = '█') -> Text:
    text = Text(no_wrap=True, overflow='crop')
    for row, line in enumerate(lines):
        for cell in line:
            if cell == front:
                text.append(cell, style=palette.accent if row % 2 == 0 else palette.secondary)
            elif cell in '╔╗╚╝║═╭╮╰╯─│┬┴├┤':
                text.append(cell, style=palette.secondary)
            elif cell in '▒░▓╱╲╳':
                text.append(cell, style=palette.line)
            else:
                text.append(cell, style=palette.muted)
        if row < len(lines) - 1:
            text.append('\n')
    return text


def _isometric_mark(width: int, palette: Palette) -> Text:
    font = BLOCK_LETTERS if width >= 62 else SMALL_LETTERS
    front = _joined_letters(font)
    depth = 2
    canvas_width = max(map(len, front)) + depth
    canvas = [[' ' for _ in range(canvas_width)] for _ in range(len(front) + depth)]
    for y, line in enumerate(front):
        for x, cell in enumerate(line):
            if cell != ' ':
                for step in range(1, depth + 1):
                    if canvas[y + step][x + step] == ' ':
                        canvas[y + step][x + step] = '░' if step == 1 else '▒'
    for y, line in enumerate(front):
        canvas[y][:len(line)] = line
    return _paint_lines([''.join(row).rstrip() for row in canvas], palette)


def _glitch_mark(width: int, palette: Palette) -> Text:
    font = BLOCK_LETTERS if width >= 62 else SMALL_LETTERS
    lines = _joined_letters(font)
    if width >= 62:
        # Two scanlines shear only the shadow/edge strokes; the ICFORGE silhouette stays legible.
        lines[1] = '  ' + lines[1]
        lines[3] = lines[3].replace('║', '╳').replace('╗', '▓', 1)
        lines[4] = lines[4] + ' ░▒'
    else:
        lines[1] = ' ' + lines[1]
        lines[3] = lines[3].replace('█', '▓', 1)
        lines[4] = lines[4] + '░'
    return _paint_lines(lines, palette)


def wordmark(width: int, theme: str = 'forge', logo: str = 'block') -> Text:
    palette = THEMES.get(theme, THEMES['forge'])
    if logo == 'minimal':
        lines = ['╭─ I C F O R G E ─╮', '╰──── DIGITAL IC ──╯']
        return _paint_lines(lines, palette, front='I')
    if logo == '3d':
        return _isometric_mark(width, palette)
    if logo == 'glitch':
        return _glitch_mark(width, palette)
    font = BLOCK_LETTERS if width >= 62 else SMALL_LETTERS
    return _paint_lines(_joined_letters(font), palette)


def chip_art(theme: str = 'forge') -> Text:
    palette = THEMES.get(theme, THEMES['forge'])
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
        text.append(line, style=palette.accent if 2 <= row <= 7 else palette.secondary)
        if row < len(lines) - 1:
            text.append('\n')
    return text


def startup_panel(*, width: int, height: int, version: str, provider: str, model: str,
                  workspace: Path, project: str, session: str, connected: bool,
                  toolchain: dict[str, bool], command_count: int,
                  theme: str = 'forge', logo: str = 'block') -> Panel:
    palette = THEMES.get(theme, THEMES['forge'])
    short = height < 36
    title = Text(f' ICFORGE v{version} ', style=f'bold {palette.accent}')
    if width < 70:
        content = Text()
        content.append(f'{provider} / {model}\n', style=palette.accent)
        content.append(f'{palette.name} · {logo}\n', style=palette.secondary)
        content.append(f'{project or workspace.name} · {session}\n\n', style=palette.muted)
        content.append('Available Tools\n', style=f'bold {palette.accent}')
        content.append('/design /cpu /verify /suite\n/td /flow /lint /review\n/package /project /connect', style=palette.foreground)
        return Panel(content, title=title, border_style=palette.line, style='on #000000',
                     box=box.ROUNDED, padding=(0, 1))

    left = Text()
    if not short and width >= 108:
        left.append_text(chip_art(theme))
        left.append('\n\n')
    left.append(model+'\n', style=f'bold {palette.accent}')
    state = 'configured' if connected else 'offline replay' if provider == 'exchange' else '/connect to configure'
    left.append(f'{provider} · {state}\n', style=palette.muted)
    left.append(f'{palette.name} · {logo}\n', style=palette.secondary)
    left.append(str(workspace)+'\n', style=palette.muted)
    left.append(f'Session: {session}', style=palette.muted)

    right = Text()
    right.append('Available Tools\n', style=f'bold {palette.accent}')
    categories = [
        ('design', '/design  /cpu  /init'),
        ('verification', '/verify  /suite  /replay'),
        ('implementation', '/synth  /td  /flow'),
        ('engineering', '/lint  /review'),
        ('delivery', '/package  /artifacts  /export'),
        ('workspace', '/project  /files  /open  /diff'),
    ]
    for category, commands in categories:
        right.append(category+': ', style=palette.muted)
        right.append(commands+'\n', style=palette.foreground)
    if not short:
        right.append('\nDesign Workflows\n', style=f'bold {palette.accent}')
        right.append('RTL design: ', style=palette.muted)
        right.append('spec → RTL → self-check simulation\n', style=palette.foreground)
        right.append('RISC-V CPU: ', style=palette.muted)
        right.append('single / multi / pipeline · RV32I\n', style=palette.foreground)
        right.append('FPGA delivery: ', style=palette.muted)
        right.append('Anlogic TD · HX1P35A\n', style=palette.foreground)
        right.append('Engineering: ', style=palette.muted)
        right.append('evidence gates · traceable review\n', style=palette.foreground)
        right.append('\nLocal Toolchain\n', style=f'bold {palette.accent}')
        for name, available in toolchain.items():
            right.append(name+': ', style=palette.muted)
            right.append('available  ' if available else 'not installed  ', style=palette.foreground if available else palette.warning)
        right.append(f'\n\n{command_count} commands · /help · /theme · /logo', style=palette.muted)
    else:
        right.rstrip()

    grid = Table.grid(padding=(0, 3))
    grid.width = max(20, width - (6 if not short else 4))
    grid.expand = True
    grid.add_column(width=34 if width >= 108 else 25, no_wrap=False, overflow='fold')
    grid.add_column(ratio=1)
    grid.add_row(left, right)
    return Panel(grid, title=title, border_style=palette.line, style='on #000000', box=box.ROUNDED,
                 padding=(1, 2) if not short else (0, 1), expand=True)
