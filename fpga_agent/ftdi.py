"""FTDI channel-B UART via the vendor D2XX API (no VCP/COM dependency)."""
from __future__ import annotations
import ctypes as c
import os
import time
from pathlib import Path


class FtdiSerial:
    def __init__(self,port: str,baudrate=115200,timeout=1,write_timeout=10,**kwargs):
        if os.name!='nt': raise RuntimeError('This D2XX transport currently supports Windows only')
        serial=port.removeprefix('ftdi://')
        if not port.startswith('ftdi://') or not serial.endswith('B') or len(serial)>15:
            raise ValueError('Select the UART channel B explicitly: ftdi://<serial>B')
        override=os.environ.get('ICFORGE_FTD2XX_DLL')
        candidates=[Path(override)] if override else []
        candidates += [Path(__file__).resolve().parents[1]/'.tools/ftdi-current/amd64/ftd2xx64.dll']
        library=next((p for p in candidates if p.is_file()),None)
        if library is None: raise RuntimeError('Set ICFORGE_FTD2XX_DLL to the official 64-bit ftd2xx DLL')
        self.dll=c.WinDLL(str(library)); self.handle=c.c_void_p()
        self.port=port; self.baudrate=baudrate; self.timeout=timeout; self.write_timeout=write_timeout
        self.is_open=False
        signatures={
            'FT_OpenEx':[c.c_void_p,c.c_uint32,c.POINTER(c.c_void_p)],
            'FT_Close':[c.c_void_p], 'FT_ResetDevice':[c.c_void_p],
            'FT_SetBaudRate':[c.c_void_p,c.c_uint32],
            'FT_SetDataCharacteristics':[c.c_void_p,c.c_ubyte,c.c_ubyte,c.c_ubyte],
            'FT_SetFlowControl':[c.c_void_p,c.c_ushort,c.c_ubyte,c.c_ubyte],
            'FT_SetTimeouts':[c.c_void_p,c.c_uint32,c.c_uint32],
            'FT_SetLatencyTimer':[c.c_void_p,c.c_ubyte],
            'FT_Purge':[c.c_void_p,c.c_uint32],
            'FT_Read':[c.c_void_p,c.c_void_p,c.c_uint32,c.POINTER(c.c_uint32)],
            'FT_Write':[c.c_void_p,c.c_void_p,c.c_uint32,c.POINTER(c.c_uint32)],
            'FT_GetQueueStatus':[c.c_void_p,c.POINTER(c.c_uint32)],
            'FT_GetStatus':[c.c_void_p,c.POINTER(c.c_uint32),c.POINTER(c.c_uint32),c.POINTER(c.c_uint32)],
        }
        for name,args in signatures.items():
            fn=getattr(self.dll,name); fn.argtypes=args; fn.restype=c.c_uint32
        self._check(self.dll.FT_OpenEx(c.cast(c.c_char_p(serial.encode('ascii')),c.c_void_p),1,c.byref(self.handle)),'open B')
        self.is_open=True
        try:
            self._call('FT_SetBaudRate',int(baudrate))
            self._call('FT_SetDataCharacteristics',8,0,0)
            self._call('FT_SetFlowControl',0,0,0)
            self._call('FT_SetTimeouts',max(1,int((timeout or 0)*1000)),max(1,int((write_timeout or 10)*1000)))
            self._call('FT_SetLatencyTimer',2)
        except Exception:
            self.close(); raise

    @staticmethod
    def _check(status,operation):
        if status: raise OSError(f'FTDI D2XX {operation} failed: FT_STATUS={status}')

    def _call(self,name,*args):
        if not self.is_open: raise OSError('FTDI UART is closed')
        self._check(getattr(self.dll,name)(self.handle,*args),name)

    @property
    def in_waiting(self):
        count=c.c_uint32(); self._call('FT_GetQueueStatus',c.byref(count)); return count.value

    def read(self,size=1):
        if size<=0: return b''
        data=c.create_string_buffer(size); count=c.c_uint32()
        self._call('FT_Read',data,size,c.byref(count))
        return data.raw[:count.value]

    def write(self,data):
        data=bytes(data); buffer=c.create_string_buffer(data); count=c.c_uint32()
        self._call('FT_Write',buffer,len(data),c.byref(count))
        if count.value!=len(data): raise TimeoutError('FTDI UART short write')
        return count.value

    def readline(self,size=-1):
        result=bytearray()
        while size<0 or len(result)<size:
            value=self.read(1)
            if not value: break
            result.extend(value)
            if value==b'\n': break
        return bytes(result)

    def reset_input_buffer(self): self._call('FT_Purge',1)
    def reset_output_buffer(self): self._call('FT_Purge',2)

    def flush(self):
        end=time.monotonic()+(self.write_timeout or 10)
        while True:
            rx=c.c_uint32(); tx=c.c_uint32(); event=c.c_uint32()
            self._call('FT_GetStatus',c.byref(rx),c.byref(tx),c.byref(event))
            if not tx.value: return
            if time.monotonic()>end: raise TimeoutError('FTDI UART flush timed out')
            time.sleep(.001)

    def close(self):
        if self.is_open:
            self.dll.FT_Close(self.handle); self.is_open=False

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
