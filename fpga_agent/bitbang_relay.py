"""Buffered local bridge for Windows OpenOCD's nonblocking socket writer.

OpenOCD 0.12 remote_bitbang treats WSAEWOULDBLOCK as fatal. A fast receive
queue decouples its burst writes from a slower USB-JTAG hardware server.
Bytes and TDO responses remain ordered and unchanged.
"""
from __future__ import annotations

import queue
import socket
import threading


class BitbangRelay:
    def __init__(self, host, port):
        self.target=(host,port)
        self.listener=socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,16*1024*1024)
        self.listener.bind(('127.0.0.1',0))
        self.listener.listen(1)
        self.listener.settimeout(30)
        self.port=self.listener.getsockname()[1]
        self.error=None
        self.forwarded_bytes=0
        self.thread=threading.Thread(target=self._run,daemon=True)

    def _run(self):
        pending=queue.Queue()
        try:
            with self.listener.accept()[0] as front, socket.create_connection(self.target,timeout=10) as back:
                back.settimeout(1200)
                def receive():
                    try:
                        while data:=front.recv(1024*1024):
                            pending.put(data)
                    except OSError as exc:
                        self.error=exc
                    finally:
                        pending.put(None)
                def respond():
                    try:
                        while data:=back.recv(65536):
                            front.sendall(data)
                    except OSError:
                        pass  # shutdown after the ordered command queue is drained
                threading.Thread(target=receive,daemon=True).start()
                threading.Thread(target=respond,daemon=True).start()
                while (data:=pending.get(timeout=1200)) is not None:
                    back.sendall(data)
                    self.forwarded_bytes+=len(data)
        except Exception as exc:
            self.error=exc
        finally:
            self.listener.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self,kind,error,tb):
        self.thread.join(timeout=1200)
        if self.thread.is_alive():
            raise TimeoutError('JTAG relay did not drain its command queue')
        if self.error and error is None:
            raise RuntimeError(f'JTAG relay failed: {self.error}')
