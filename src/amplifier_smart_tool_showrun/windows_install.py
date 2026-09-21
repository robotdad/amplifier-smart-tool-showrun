"""Pinned Windows x64 companion acquisition, including its .NET runtime."""
import asyncio
import hashlib
import os
import platform
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .errors import ShowrunError, require
from .windows_desktop import helper_path, powershell, quote

RELEASE = 'desktop-v0.2.0'
ASSET = 'showrun-desktop-windows-x64.zip'
SHA256 = '124fe1be58fb5d469ceb80f4af4234a24274a1691fe96496c89352d62b0766b2'
URL = f'https://github.com/robotdad/amplifier-smart-tool-showrun/releases/download/{RELEASE}/{ASSET}'
LIMIT = 256 * 1024 * 1024


def stage(destination):
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read(LIMIT + 1)
    require(len(data) <= LIMIT, 'Companion download exceeded its size limit.', 'desktop_install_failed')
    require(hashlib.sha256(data).hexdigest() == SHA256,
            'Companion checksum mismatch; the installed companion was not changed.', 'desktop_checksum_mismatch')
    archive = destination.parent / ASSET
    archive.write_bytes(data)
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        require(len(entries) == 1 and entries[0].filename == 'ShowrunDesktop.exe'
                and not entries[0].is_dir() and not stat.S_ISLNK(entries[0].external_attr >> 16)
                and 0 < entries[0].file_size <= LIMIT,
                'Unexpected Windows companion archive.', 'desktop_install_failed')
        executable = bundle.read(entries[0])
        require(executable.startswith(b'MZ'), 'Invalid Windows companion executable.', 'desktop_install_failed')
        destination.write_bytes(executable)


async def allow_interactive_launch(path):
    # SSH may create the private staging file with Administrators as owner.
    # Explicitly grant the actual user's SID RX before moving it into place,
    # so the same user's limited interactive token can launch the companion.
    await powershell(
        '$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; '
        f'$acl=Get-Acl -LiteralPath {quote(path)}; '
        '$rule=[System.Security.AccessControl.FileSystemAccessRule]::new($sid, '
        '[System.Security.AccessControl.FileSystemRights]::ReadAndExecute, '
        '[System.Security.AccessControl.AccessControlType]::Allow); '
        '$acl.AddAccessRule($rule); '
        f'Set-Acl -LiteralPath {quote(path)} -AclObject $acl')


async def install():
    require(sys.platform == 'win32' and platform.machine().lower() in {'amd64', 'x86_64'},
            'This early-access binary requires Windows x64. Use --build for development.', 'desktop_unsupported')
    destination = helper_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.install-', dir=destination.parent) as temporary:
            staged = Path(temporary) / destination.name
            await asyncio.to_thread(stage, staged)
            await allow_interactive_launch(staged)
            # A locked/running executable fails before replacing the installed file.
            os.replace(staged, destination)
    except ShowrunError:
        raise
    except Exception:
        raise ShowrunError('desktop_install_failed', 'Windows companion acquisition or replacement failed.',
                           'Close active Showrun takes, check network access, and retry prepare-desktop.') from None
    return {'status': 'prepared', 'helper': str(destination), 'release': RELEASE, 'sha256': SHA256,
            'model_calls': 0, 'notice': 'Unsigned early-access Windows x64 binary. .NET runtime included.'}
