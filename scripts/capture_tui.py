"""Capture the real Textual workspace; --verify runs Icarus before capturing."""
from __future__ import annotations

import argparse
import asyncio
import html
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fpga_agent.tui import ForgeApp


async def capture(verify: bool) -> None:
    os.environ.pop("NO_COLOR", None)
    os.environ.update(TERM="xterm-256color", COLORTERM="truecolor")
    app = ForgeApp(ROOT)
    app.settings.mode = "build"
    async with app.run_test(size=(156, 46)) as pilot:
        project = ROOT / "benchmarks/rv32_pipeline"
        await app.select_project(project)
        if verify:
            await app.add_message("user", "/verify")
            await app.submit_command("/verify")
            for _ in range(1200):
                await pilot.pause(0.05)
                if not app.busy:
                    break
            if app.busy:
                await app.action_cancel_task()
                raise RuntimeError("Verification did not complete in time")
            await app.add_message("user", "/artifacts")
            await app.submit_command("/artifacts")
            app.query_one("#logs").display = False
        await pilot.pause()
        report = ROOT / "reports"
        report.mkdir(exist_ok=True)
        app.save_screenshot("tui.svg", path=str(report))
    import resvg_py
    from rich.cells import cell_len
    svg = (ROOT / "reports/tui.svg").read_text(encoding="utf-8")
    svg = svg.replace("font-family: Fira Code, monospace;", "font-family: Consolas, Microsoft YaHei, monospace;")
    # Rich SVG export uses codepoint lengths for textLength; full-width CJK
    # needs terminal cell widths when rendering the standalone PNG.
    def wide_text(match):
        tag, content = match.groups()
        value = html.unescape(content)
        if any(ord(char) > 255 for char in value):
            tag = re.sub(r'textLength="[^"]*"', f'textLength="{cell_len(value)*12.2:g}"', tag)
        return tag + content + "</text>"
    svg = re.sub(r"(<text\b[^>]*>)([^<]*)</text>", wide_text, svg)
    target = ROOT / "docs/images" / ("tui-verified.png" if verify else "tui.png")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resvg_py.svg_to_bytes(svg_string=svg))
    print(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    asyncio.run(capture(parser.parse_args().verify))
