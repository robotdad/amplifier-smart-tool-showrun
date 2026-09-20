"""Real local process identity plus Windows handle protocol without a Windows host."""
import os
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from amplifier_smart_tool_showrun import process_platform as native
from amplifier_smart_tool_showrun.stories_helper import exited, process_identity, same_process, signal_owned


def test_current_and_exited_process_identity():
    own = process_identity(os.getpid())
    assert same_process(own)
    assert not same_process({**own, 'unknown': 'different identity'})
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        saved = process_identity(child.pid)
        assert same_process(saved)
    finally:
        child.terminate()
        child.wait(timeout=5)
    assert not same_process(saved)
    assert exited(child.pid)


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS native identity')
def test_macos_creation_time_and_no_pid_kill(monkeypatch):
    info = native.mac_info(os.getpid())
    assert info.pid == os.getpid() and info.start_sec > 0
    identity = process_identity(os.getpid())
    assert identity['start_microseconds'] == info.start_usec
    monkeypatch.setattr(os, 'kill', lambda *args: pytest.fail('Never kill an unbound macOS PID'))
    assert signal_owned(identity) is False


def test_windows_termination_uses_same_verified_handle(monkeypatch):
    expected = {'platform': 'win32', 'pid': 123, 'creation_filetime': 456}
    calls = []
    api = SimpleNamespace(TerminateProcess=lambda h, code: calls.append((h, code)) or True)

    @contextmanager
    def handle(pid, terminate=False):
        assert pid == 123 and terminate
        yield api, 987

    monkeypatch.setattr(native, 'windows_handle', handle)
    monkeypatch.setattr(native, 'windows_identity', lambda a, h, p: expected)
    assert native.terminate_windows(expected)
    assert calls == [(987, 1)]
    calls.clear()
    assert not native.terminate_windows({**expected, 'creation_filetime': 789})
    assert calls == []


def test_windows_denied_handle_is_not_termination(monkeypatch):
    @contextmanager
    def denied(*args, **kwargs):
        raise PermissionError()
        yield

    monkeypatch.setattr(native, 'windows_handle', denied)
    assert not native.terminate_windows({'pid': 123})
