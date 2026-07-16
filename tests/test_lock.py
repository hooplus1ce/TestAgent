from drissionpage_mcp.core.lock import _RWLock


def test_write_lock_timeout_does_not_leave_waiter_registered():
    lock = _RWLock()
    lock.acquire_read()
    try:
        assert lock.acquire_write(timeout=0) is False
    finally:
        lock.release_read()

    assert lock.acquire_write(timeout=0) is True
    lock.release_write()
