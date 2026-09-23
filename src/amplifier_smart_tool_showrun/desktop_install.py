"""Explicit, pinned early-access companion acquisition; no implicit downloads."""
import asyncio
import hashlib
import platform
import plistlib
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from .capture import command
from .desktop import BUNDLE_ID, _prepared, app_path
from .errors import ShowrunError, require

RELEASE = 'desktop-v0.5.0'
ASSET = 'showrun-desktop-macos-arm64.zip'
SHA256 = '74fd9be87e6474f534225605d1766f6af00b316d6b65cfe690dfb29a2f887aea'
URL = f'https://github.com/robotdad/amplifier-smart-tool-showrun/releases/download/{RELEASE}/{ASSET}'
LIMIT = 50 * 1024 * 1024


def fetch(destination):
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(LIMIT + 1)
    require(len(data) <= LIMIT, 'Companion download exceeded its size limit.', 'desktop_install_failed')
    require(hashlib.sha256(data).hexdigest() == SHA256,
            'Companion checksum mismatch; the installed app was not changed.', 'desktop_checksum_mismatch')
    destination.write_bytes(data)


def unpack(archive, folder):
    with zipfile.ZipFile(archive) as bundle:
        total = 0
        for item in bundle.infolist():
            path = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            require(not path.is_absolute() and '..' not in path.parts
                    and path.parts and path.parts[0] == 'Showrun Desktop.app'
                    and not stat.S_ISLNK(mode), 'Unsafe companion archive.', 'desktop_install_failed')
            total += item.file_size
            require(total <= LIMIT, 'Companion archive is too large.', 'desktop_install_failed')
            target = folder.joinpath(*path.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(item))
                target.chmod(0o755 if mode & 0o111 else 0o644)


async def install():
    require(sys.platform == 'darwin' and platform.machine() == 'arm64',
            'The early-access binary requires Apple Silicon macOS 14+. Use --build for development.',
            'desktop_unsupported')
    destination = app_path()
    if destination.exists():
        try:
            metadata = plistlib.loads((destination / 'Contents/Info.plist').read_bytes())
        except (OSError, ValueError):
            metadata = {}
        require(metadata.get('CFBundleIdentifier') == BUNDLE_ID,
                'Another application occupies the installation path.', 'desktop_install_conflict')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.showrun-install-', dir=destination.parent) as temporary:
            folder = Path(temporary)
            archive = folder / ASSET
            await asyncio.to_thread(fetch, archive)
            unpack(archive, folder)
            staged = folder / destination.name
            metadata = plistlib.loads((staged / 'Contents/Info.plist').read_bytes())
            require(metadata.get('CFBundleIdentifier') == BUNDLE_ID,
                    'Unexpected companion identity.', 'desktop_install_failed')
            await command('/usr/bin/codesign', '--verify', '--strict', str(staged), timeout=30)
            backup = folder / 'previous.app'
            if destination.exists():
                destination.rename(backup)
            try:
                staged.rename(destination)
            except BaseException:
                if backup.exists():
                    backup.rename(destination)
                raise
    except ShowrunError:
        raise
    except Exception:
        raise ShowrunError('desktop_install_failed', 'Companion download or installation failed.',
                           'Check network access and the published release; --build is the developer alternative.') from None
    return dict(_prepared(), release=RELEASE, sha256=SHA256)
