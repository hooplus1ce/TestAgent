"""全局读写锁，写优先。写操作互斥，且阻塞所有读操作。"""

import threading


class _RWLock:
    """读-写锁：多读单写，写优先防止读饿死写。"""
    def __init__(self):
        self._lock = threading.Lock()
        self._readers = 0
        self._writers_waiting = 0
        self._writing = False
        self._writer_owner = None
        self._writer_depth = 0
        self._can_read = threading.Condition(self._lock)
        self._can_write = threading.Condition(self._lock)

    def acquire_read(self):
        with self._lock:
            if self._writing and self._writer_owner == threading.get_ident():
                self._readers += 1
                return
            while self._writers_waiting > 0 or self._writing:
                self._can_read.wait()
            self._readers += 1

    def release_read(self):
        with self._lock:
            self._readers -= 1
            if self._readers == 0:
                self._can_write.notify()

    def acquire_write(self):
        with self._lock:
            current = threading.get_ident()
            if self._writing and self._writer_owner == current:
                self._writer_depth += 1
                return
            self._writers_waiting += 1
            while self._readers > 0 or self._writing:
                self._can_write.wait()
            self._writing = True
            self._writer_owner = current
            self._writer_depth = 1
            self._writers_waiting -= 1

    def release_write(self):
        with self._lock:
            if self._writer_owner != threading.get_ident():
                raise RuntimeError("write lock released by a non-owner")
            self._writer_depth -= 1
            if self._writer_depth:
                return
            self._writing = False
            self._writer_owner = None
            self._can_read.notify_all()
            self._can_write.notify()


# 全局读写锁实例，供 server.py 和需要手动管理锁的各服务共用
_rwlock = _RWLock()
