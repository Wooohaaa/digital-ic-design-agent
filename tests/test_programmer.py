import hashlib
import socket
import threading
import unittest
from unittest.mock import patch

from fpga_agent.bitbang_relay import BitbangRelay
from fpga_agent.programmer import probe_chain


class ProgrammerTests(unittest.TestCase):
    def scan(self, words):
        class Connection:
            data=bytearray(''.join(f'{v:032b}'[::-1] for v in words).encode())
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def sendall(self,data): pass
            def recv(self,count):
                data=bytes(self.data[:min(count,7)])
                del self.data[:len(data)]
                return data
        with patch('fpga_agent.programmer.socket.create_connection',return_value=Connection()):
            return probe_chain('127.0.0.1',5555)

    def test_scan_distinguishes_actual_single_and_dual_tap(self):
        self.assertEqual(self.scan([0x4808f0e7,0]),[0x4808f0e7])
        self.assertEqual(self.scan([0x10300a6d,0x4808f0e7]),[0x10300a6d,0x4808f0e7])
        with self.assertRaisesRegex(RuntimeError,'Unexpected JTAG chain'):
            self.scan([0x10300a6d,0])

    def test_buffered_relay_preserves_large_writes_and_responses(self):
        payload=bytes(range(256))*8192
        expected=hashlib.sha256(payload).digest()
        listener=socket.socket()
        listener.bind(('127.0.0.1',0)); listener.listen(1)
        listener.settimeout(5)
        errors=[]
        def server():
            try:
                with listener.accept()[0] as peer:
                    peer.settimeout(5)
                    data=bytearray()
                    while len(data)<len(payload):
                        chunk=peer.recv(min(65536,len(payload)-len(data)))
                        if not chunk: raise RuntimeError('premature EOF')
                        data.extend(chunk)
                    peer.sendall(hashlib.sha256(data).digest())
            except Exception as exc:
                errors.append(exc)
            finally:
                listener.close()
        worker=threading.Thread(target=server,daemon=True); worker.start()
        with BitbangRelay('127.0.0.1',listener.getsockname()[1]) as relay:
            with socket.create_connection(('127.0.0.1',relay.port),timeout=5) as front:
                front.sendall(payload)
                received=bytearray()
                while len(received)<32:
                    chunk=front.recv(32-len(received))
                    if not chunk: break
                    received.extend(chunk)
                self.assertEqual(bytes(received),expected)
        worker.join(5)
        self.assertFalse(errors)
        self.assertEqual(relay.forwarded_bytes,len(payload))


if __name__ == '__main__': unittest.main()
