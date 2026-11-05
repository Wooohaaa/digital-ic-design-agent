"""OS-held exclusive ownership of a board across programming and observation."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import threading

_thread_lock=threading.RLock()
_state=threading.local()


@contextmanager
def board_lease(host='127.0.0.1',port=5555):
    key=f'hx1p35a:{host}:{port}'
    if not _thread_lock.acquire(blocking=False):
        raise RuntimeError('Hardware board is busy in another operation')
    stream=None;locked=False
    try:
        if getattr(_state,'key',None):
            if _state.key!=key:raise RuntimeError('Cannot nest ownership of different boards')
            yield
            return
        folder=Path(tempfile.gettempdir())/'icforge-board-locks';folder.mkdir(exist_ok=True)
        stream=(folder/(hashlib.sha256(key.encode()).hexdigest()+'.lock')).open('a+b')
        if stream.seek(0,2)==0:stream.write(b'\0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('Hardware board is busy in another IC Forge process; wait for its operation to finish') from exc
        locked=True;_state.key=key
        yield
    finally:
        if locked:
            _state.key=None
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        if stream:stream.close()
        _thread_lock.release()
