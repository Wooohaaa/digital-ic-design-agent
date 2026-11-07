from __future__ import annotations

import ctypes
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.ftdi import FtdiSerial


class _Function:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _FakeD2xx:
    def __init__(self):
        self.read_data = b"uart"
        self.FT_OpenEx = _Function(self.open)
        self.FT_Close = _Function(lambda handle: 0)
        self.FT_ResetDevice = _Function(lambda handle: 0)
        self.FT_SetBaudRate = _Function(lambda *args: 0)
        self.FT_SetDataCharacteristics = _Function(lambda *args: 0)
        self.FT_SetFlowControl = _Function(lambda *args: 0)
        self.FT_SetTimeouts = _Function(lambda *args: 0)
        self.FT_SetLatencyTimer = _Function(lambda *args: 0)
        self.FT_Purge = _Function(lambda *args: 0)
        self.FT_Read = _Function(self.read)
        self.FT_Write = _Function(self.write)
        self.FT_GetQueueStatus = _Function(self.queue_status)
        self.FT_GetStatus = _Function(self.status)

    @staticmethod
    def open(serial, flags, output_handle):
        ctypes.cast(output_handle, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(99)
        return 0

    def read(self, handle, buffer, size, actual):
        count = min(size, len(self.read_data))
        if count:
            ctypes.memmove(buffer, self.read_data, count)
        ctypes.cast(actual, ctypes.POINTER(ctypes.c_uint32))[0] = count
        self.read_data = self.read_data[count:]
        return 0

    @staticmethod
    def write(handle, buffer, size, actual):
        ctypes.cast(actual, ctypes.POINTER(ctypes.c_uint32))[0] = size
        return 0

    @staticmethod
    def queue_status(handle, count):
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 4
        return 0

    @staticmethod
    def status(handle, rx, tx, event):
        for pointer in (rx, tx, event):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))[0] = 0
        return 0


class FtdiSerialTests(unittest.TestCase):
    def test_nonzero_vendor_status_is_reported(self):
        with self.assertRaisesRegex(OSError, "FT_STATUS=3"):
            FtdiSerial._check(3, "open B")

    @unittest.skipUnless(os.name == "nt", "D2XX transport is Windows-only")
    def test_channel_b_read_write_and_context_close(self):
        with tempfile.TemporaryDirectory() as folder:
            dll_path = Path(folder) / "ftd2xx64.dll"
            dll_path.write_bytes(b"test DLL placeholder")
            fake = _FakeD2xx()
            with patch.object(ctypes, "WinDLL", return_value=fake, create=True), \
                 patch.dict(os.environ, {"ICFORGE_FTD2XX_DLL": str(dll_path)}):
                with FtdiSerial("ftdi://ABC123B") as serial:
                    self.assertTrue(serial.is_open)
                    self.assertEqual(serial.in_waiting, 4)
                    self.assertEqual(serial.read(4), b"uart")
                    self.assertEqual(serial.write(b"ping"), 4)
                    serial.flush()
                self.assertFalse(serial.is_open)


if __name__ == "__main__":
    unittest.main()
