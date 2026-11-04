from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from fpga_agent.tinyllm import PINS
    from fpga_agent.tinyllm_hardware import _UART, _preflight
except ImportError:
    PINS = None

from fpga_agent.programmer import program_sram
from fpga_agent.project import create_project


@unittest.skipIf(PINS is None, 'optional tinyllm dependencies unavailable')
class TinyLLMSafetyTests(unittest.TestCase):
    def test_model_flash_never_uses_configuration_flash_pins(self):
        configuration = {'R15','R8','U12','V12','V11','U11'}
        user_flash = {pin for signal,pin in PINS.items() if 'flash' in signal}
        self.assertTrue(configuration.isdisjoint(user_flash))
        self.assertEqual(user_flash,{'D14','D16','E13','N6','C16','A13'})

    def test_firmware_error_ack_aborts_upload(self):
        class Port:
            data = iter(b'ERROR flash-timeout\n+')
            def read(self, count):
                return bytes([next(self.data)])
        with self.assertRaisesRegex(RuntimeError,'flash-timeout'):
            _UART(Port(),[]).read_page_ack(0)

    def test_missing_hardware_preconditions_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            project=create_project('blocked',Path(folder)/'blocked')
            with self.assertRaisesRegex(RuntimeError,'required evidence'):
                _preflight(project)
            with patch('fpga_agent.programmer.subprocess.run') as spawn:
                with self.assertRaisesRegex(ValueError,'simulation gate'):
                    program_sram(project)
                spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
