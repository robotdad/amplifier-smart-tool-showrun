"""Native process identity on macOS and Windows; no shell or PID signal probes.

Linux's existing receipt format lives in stories_helper for compatibility. These
identities use the OS creation timestamp, never a rounded display from `ps`.
"""

import ctypes
import errno
import sys
from contextlib import contextmanager


class BsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        'nfiles', 'pgid', 'jobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


def mac_info(pid):
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    info = BsdInfo()
    size = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if size != ctypes.sizeof(info):
        code = ctypes.get_errno() or errno.ESRCH
        if code == errno.ESRCH:
            raise ProcessLookupError(pid)
        raise OSError(code, 'Cannot inspect process identity')
    if info.pid != pid or not info.start_sec:
        raise OSError('Invalid process identity')
    return info


def mac_boot_id():
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                ctypes.c_void_p, ctypes.c_size_t]
    lib.sysctlbyname.restype = ctypes.c_int
    value = ctypes.create_string_buffer(128)
    size = ctypes.c_size_t(len(value))
    if lib.sysctlbyname(b'kern.bootsessionuuid', value, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot read boot session identity')
    result = value.value.decode('ascii')
    if not result:
        raise OSError('Missing boot session identity')
    return result


def windows_api():
    from ctypes import wintypes

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    return api


@contextmanager
def windows_handle(pid, terminate=False):
    api = windows_api()
    handle = api.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0), False, pid)
    if not handle:
        code = ctypes.get_last_error()
        if code == 87:
            raise ProcessLookupError(pid)
        raise OSError(code, 'Cannot open process handle')
    try:
        yield api, handle
    finally:
        api.CloseHandle(handle)


def windows_identity(api, handle, pid):
    from ctypes import wintypes

    creation, exit_time, kernel, user = [wintypes.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in (creation, exit_time, kernel, user))):
        raise OSError('Cannot read process creation time')
    return {'platform': 'win32', 'pid': pid,
            'creation_filetime': (creation.dwHighDateTime << 32) | creation.dwLowDateTime}


def identity(pid, allow_exited=False):
    if type(pid) is not int or pid <= 0:
        raise ValueError('Invalid PID')
    if sys.platform == 'darwin':
        info = mac_info(pid)
        if info.status == 5 and not allow_exited:  # SZOMB
            raise ProcessLookupError(pid)
        return {'platform': 'darwin', 'pid': pid, 'boot_id': mac_boot_id(),
                'start_seconds': info.start_sec, 'start_microseconds': info.start_usec}
    if sys.platform == 'win32':
        with windows_handle(pid) as (api, handle):
            state = api.WaitForSingleObject(handle, 0)
            if state not in (0, 258):
                raise OSError('Cannot determine process state')
            if state == 0 and not allow_exited:
                raise ProcessLookupError(pid)
            return windows_identity(api, handle, pid)
    raise OSError('Unsupported process platform')


def exited(pid):
    try:
        identity(pid)
        return False
    except ProcessLookupError:
        return True


def terminate_windows(expected):
    """Check identity and terminate through the SAME handle, immune to PID reuse."""
    try:
        with windows_handle(expected['pid'], terminate=True) as (api, handle):
            if windows_identity(api, handle, expected['pid']) != expected:
                return False
            return bool(api.TerminateProcess(handle, 1))
    except (OSError, ValueError, KeyError, TypeError):
        return False
