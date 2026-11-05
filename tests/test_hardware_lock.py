import subprocess
import sys
import unittest
from fpga_agent.hardware_lock import board_lease


class BoardLeaseTests(unittest.TestCase):
    def test_nested_same_process_and_cross_process_exclusion(self):
        code='from fpga_agent.hardware_lock import board_lease\nwith board_lease(port=65530): pass'
        with board_lease(port=65530):
            with board_lease(port=65530):pass
            result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('busy',result.stderr)
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_crashed_owner_does_not_leave_a_stale_board_lease(self):
        code='import os\nfrom fpga_agent.hardware_lock import board_lease\nwith board_lease(port=65529): os._exit(0)'
        subprocess.run([sys.executable,'-c',code],check=True,timeout=10)
        with board_lease(port=65529):pass


if __name__=='__main__':unittest.main()
