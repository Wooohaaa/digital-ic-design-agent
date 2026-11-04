from io import StringIO
from pathlib import Path
import unittest

from rich.cells import cell_len
from rich.console import Console

from fpga_agent.branding import wordmark, startup_panel


class TerminalBrandingTests(unittest.TestCase):
    def test_block_lettering_remains_multiline_at_small_terminal_widths(self):
        for width in (40,58,78,152):
            with self.subTest(width=width):
                lines=wordmark(width).plain.splitlines()
                self.assertGreaterEqual(len(lines),5)
                self.assertTrue(all(cell_len(line)<=width for line in lines))
                self.assertIn('█',lines[0])

    def test_startup_card_fits_terminal_and_reports_actual_tool_availability(self):
        for width,height in ((40,24),(58,28),(78,24),(106,36),(152,46)):
            with self.subTest(size=(width,height)):
                stream=StringIO()
                console=Console(file=stream,width=width,color_system=None)
                console.print(startup_panel(width=width,height=height,version='test',provider='api',
                    model='a-long-model-name-for-terminal-layout',workspace=Path('workspace/long-project-directory'),
                    project='demo',session='0123456789ab',connected=False,
                    toolchain={'Icarus':True,'Verilator':False},command_count=30))
                rendered=stream.getvalue()
                self.assertTrue(all(cell_len(line)<=width for line in rendered.splitlines()))
                self.assertIn('Available Tools',rendered)
                if width>=106:
                    self.assertIn('not installed',rendered)
                    self.assertIn('Design Workflows',rendered)


if __name__=='__main__':
    unittest.main()
